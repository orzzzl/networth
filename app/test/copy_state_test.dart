import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/clock_continuity.dart';
import 'package:networth_app/src/domain/copy_freshness.dart';
import 'package:networth_app/src/domain/fetch_diagnostics.dart';
import 'package:networth_app/src/domain/publication_seq.dart';
import 'package:networth_app/src/domain/seq_baseline.dart';

/// A clock this device has proved continuous: the same interval measured two
/// ways, agreeing exactly. The neutral value for tests about *age*, which is a
/// different question from whether the clock can be trusted at all.
const ClockContinuity trustedClock =
    ContinuityHeld(wallElapsed: Duration.zero, monotonicElapsed: Duration.zero);

const String _pairing = 'pairing-a';
const Duration _interval = Duration(seconds: 86400);
const Duration _grace = Duration(seconds: 21600);
final DateTime _published = DateTime.utc(2026, 9, 15, 4);

/// `published_at + 86400 + 21600`, asserted in the suite below rather than
/// assumed here.
final DateTime _deadline = DateTime.utc(2026, 9, 16, 10);
final PublicationSeq _held = PublicationSeq.parse('41');

CopyState _evaluate({
  required DateTime deviceNow,
  DateTime? publishedAt,
  DiagnosticsState diagnostics = const DiagnosticsAbsent(),
  BaselineState? baseline,
  ClockContinuity continuity = trustedClock,
}) =>
    evaluateCopyState(
      publishedAt: publishedAt ?? _published,
      publishInterval: _interval,
      grace: _grace,
      deviceNow: deviceNow,
      continuity: continuity,
      diagnostics: diagnostics,
      baseline: baseline ?? BaselineHeld(SeqBaseline(pairingId: _pairing, lastSeq: _held)),
    );

/// A phone whose last attempt succeeded at [at], returning [seq].
DiagnosticsState _succeededAt(DateTime at, {PublicationSeq? seq}) => DiagnosticsHeld(
      FetchDiagnostics.succeeded(pairingId: _pairing, at: at, seq: seq ?? _held),
    );

/// A phone whose last attempt failed at [at], after the success in [after].
DiagnosticsState _failedAt(
  DateTime at, {
  FetchFailureClass error = FetchFailureClass.offline,
  DateTime? afterSuccessAt,
}) =>
    DiagnosticsHeld(
      FetchDiagnostics.failed(
        pairingId: _pairing,
        at: at,
        error: error,
        after: afterSuccessAt == null
            ? null
            : FetchSuccess(at: afterSuccessAt, seq: _held),
      ),
    );

CannotCheckCause _cannotCheck(CopyState state) {
  final stale = state as CopyStale;
  return (stale.reason as CannotCheck).cause;
}

void main() {
  group('DESIGN section 9.1 rule 1: the clocks disagree', () {
    test('a payload from the future cannot be aged', () {
      expect(
        (_evaluate(deviceNow: _published.subtract(const Duration(minutes: 6))) as CopyUnknown)
            .disagreement,
        ClockDisagreement.payloadFromTheFuture,
      );
      // Exactly the 5 minutes of tolerance is not yet a disagreement.
      expect(
        _evaluate(deviceNow: _published.subtract(const Duration(minutes: 5))),
        isA<CopyFresh>(),
      );
    });

    test('a device clock earlier than this device own last attempt moved backwards', () {
      // No monotonic clock is involved: `last_fetch_attempt_at` was stamped by
      // this same clock, so a `device_now` before it is proof it went back.
      final state = _evaluate(
        deviceNow: DateTime.utc(2026, 9, 15, 12),
        continuity: trustedClock,
        diagnostics: _succeededAt(DateTime.utc(2026, 9, 15, 18)),
      );
      expect((state as CopyUnknown).disagreement, ClockDisagreement.deviceClockMovedBackwards);
    });

    test('the same instant is not backwards', () {
      final at = DateTime.utc(2026, 9, 15, 12);
      expect(_evaluate(deviceNow: at, diagnostics: _succeededAt(at)), isA<CopyFresh>());
    });

    test('a failed attempt stamps the clock too, so it also catches the jump', () {
      final state = _evaluate(
        deviceNow: DateTime.utc(2026, 9, 15, 12),
        continuity: trustedClock,
        diagnostics: _failedAt(DateTime.utc(2026, 9, 15, 18)),
      );
      expect((state as CopyUnknown).disagreement, ClockDisagreement.deviceClockMovedBackwards);
    });

    test('rule 1 is evaluated before the age, so a backwards clock never reads fresh', () {
      // The harm this branch exists to stop: the clock goes back a day, the
      // copy is long past its deadline, and rule 2 would call it fresh.
      final state = _evaluate(
        deviceNow: DateTime.utc(2026, 9, 15, 12),
        continuity: trustedClock,
        diagnostics: _succeededAt(DateTime.utc(2026, 9, 17, 9)),
      );
      expect(state, isA<CopyUnknown>());
      expect(state.freshness, CopyFreshness.unknown);
    });

    test('a phone with no readable history claims no clock regression', () {
      // The check needs an instant this device stamped. Absent one, silence is
      // the honest answer — not a disagreement nothing supports.
      for (final diagnostics in <DiagnosticsState>[
        const DiagnosticsAbsent(),
        const DiagnosticsUnreadable('damaged'),
      ]) {
        expect(
          _evaluate(deviceNow: DateTime.utc(2026, 9, 15, 12), diagnostics: diagnostics),
          isA<CopyFresh>(),
        );
      }
    });
  });

  group('DESIGN section 9.1 rule 1: clock continuity is evidence, not an assumption', () {
    // Two arguments for skipping a monotonic source reached this file and both
    // were wrong in the case the feature exists for. Each is pinned below by
    // the counterexample that killed it, so neither can come back quietly.

    test('a nine-day-old copy is not fresh because the clock was moved back', () {
      // The original. Published and fetched Sep 1 12:00; nine days pass with the
      // app closed, so there is no later attempt to compare against; the clock
      // is then corrected backwards to Sep 1 13:00. Every wall-clock reading is
      // self-consistent and the copy is nine days old.
      final fetched = DateTime.utc(2026, 9, 1, 12);

      final state = _evaluate(
        publishedAt: fetched,
        deviceNow: DateTime.utc(2026, 9, 1, 13),
        diagnostics: _succeededAt(fetched),
        continuity: const ContinuityHeld(
          wallElapsed: Duration(hours: 1),
          monotonicElapsed: Duration(days: 9, hours: 1),
        ),
      );

      expect(state, isA<CopyUnknown>());
      expect(
        (state as CopyUnknown).disagreement,
        ClockDisagreement.deviceClockMovedBackwards,
      );
    });

    test('and a successful fetch of a frozen publication does not repair it', () {
      // The second argument: *the host is a second clock, so a successful fetch
      // corroborates ours*. `published_at` is the host's clock at PUBLICATION,
      // not at this response — so a reachable host whose publisher has stopped
      // serves the same old publication, and the fetch corroborates nothing.
      // That is exactly the HOST_NOT_PUBLISHING case, which is why this failed
      // precisely where it was needed.
      final frozen = DateTime.utc(2026, 9, 1, 12);

      final state = _evaluate(
        publishedAt: frozen,
        deviceNow: DateTime.utc(2026, 9, 1, 13),
        // A fetch that SUCCEEDED, moments ago by the device's own clock,
        // returning the same seq it already held.
        diagnostics: _succeededAt(DateTime.utc(2026, 9, 1, 12, 59), seq: _held),
        continuity: const ContinuityHeld(
          wallElapsed: Duration(minutes: 1),
          monotonicElapsed: Duration(days: 9, minutes: 1),
        ),
        baseline: BaselineHeld(SeqBaseline(pairingId: _pairing, lastSeq: _held)),
      );

      expect(state, isA<CopyUnknown>(), reason: 'the frozen publication is not host NOW');
    });

    test('an unadjusted clock is trusted, which is what makes the above mean anything',
        () {
      // The control. Same shape, drift zero: this must still answer FRESH, or
      // the two tests above would pass against a predicate that simply never
      // says fresh.
      final fetched = DateTime.utc(2026, 9, 15, 6);

      expect(
        _evaluate(
          deviceNow: DateTime.utc(2026, 9, 15, 12),
          diagnostics: _succeededAt(fetched),
          continuity: const ContinuityHeld(
            wallElapsed: Duration(hours: 6),
            monotonicElapsed: Duration(hours: 6),
          ),
        ),
        isA<CopyFresh>(),
      );
    });

    test('time the device spent suspended still ages the copy', () {
      // The monotonic source must count suspended time. A source that stopped
      // while the phone slept would report a small elapsed interval and read a
      // week of sleep as a backwards clock — so this pins the agreeing case,
      // where wall and monotonic both say a week, and the answer is STALE
      // rather than UNKNOWN.
      final state = _evaluate(
        deviceNow: DateTime.utc(2026, 9, 22),
        diagnostics: _succeededAt(DateTime.utc(2026, 9, 15)),
        continuity: const ContinuityHeld(
          wallElapsed: Duration(days: 7),
          monotonicElapsed: Duration(days: 7),
        ),
      );

      expect(state, isA<CopyStale>());
    });

    test('drift within the sampling tolerance is not an adjustment', () {
      expect(
        _evaluate(
          deviceNow: DateTime.utc(2026, 9, 15, 12),
          diagnostics: _succeededAt(DateTime.utc(2026, 9, 15, 6)),
          continuity: const ContinuityHeld(
            wallElapsed: Duration(hours: 6),
            monotonicElapsed: Duration(hours: 6, seconds: 2),
          ),
        ),
        isA<CopyFresh>(),
        reason: 'the two readings are taken at different points in one call',
      );
      expect(
        _evaluate(
          deviceNow: DateTime.utc(2026, 9, 15, 12),
          diagnostics: _succeededAt(DateTime.utc(2026, 9, 15, 6)),
          continuity: const ContinuityHeld(
            wallElapsed: Duration(hours: 6),
            monotonicElapsed: Duration(hours: 6, seconds: 3),
          ),
        ),
        isA<CopyUnknown>(),
      );
    });

    test('a clock moved FORWARDS is also unknown, not conservatively stale', () {
      // The forward direction only over-reports staleness, so it is tempting to
      // let it through. Rule 1 asks whether the clock can be trusted, not
      // whether this particular error happens to land safely — a phone that has
      // been silently re-timed does not know the age of what it holds.
      expect(
        _evaluate(
          deviceNow: DateTime.utc(2026, 9, 22),
          diagnostics: _succeededAt(DateTime.utc(2026, 9, 15)),
          continuity: const ContinuityHeld(
            wallElapsed: Duration(days: 7),
            monotonicElapsed: Duration(minutes: 5),
          ),
        ),
        isA<CopyUnknown>(),
      );
    });

    for (final gap in ContinuityGap.values) {
      test('no verdict rests on a clock whose continuity is $gap', () {
        // Fail-closed, and **it blocks host blame as well as freshness**. A
        // predicate that skipped this for the stale branch would still accuse
        // the host of not publishing, on a deadline it computed with a clock it
        // could not vouch for — the misattribution 9.1 exists to prevent, made
        // with more confidence rather than less.
        for (final deviceNow in <DateTime>[
          DateTime.utc(2026, 9, 15, 12), // would be FRESH
          DateTime.utc(2026, 9, 20), // would be STALE, HOST_NOT_PUBLISHING
        ]) {
          final state = _evaluate(
            deviceNow: deviceNow,
            diagnostics: _succeededAt(DateTime.utc(2026, 9, 19)),
            continuity: ContinuityUnknown(gap),
          );

          expect(state, isA<CopyUnknown>(), reason: '$gap at $deviceNow');
          expect(
            (state as CopyUnknown).disagreement,
            ClockDisagreement.clockContinuityUnknown,
            reason: 'absence of evidence is its own answer, not a detected fault',
          );
        }
      });
    }
  });

  group('DESIGN section 9.1 rule 2: the deadline', () {
    test('the deadline travels with the payload', () {
      expect(
        staleAfter(publishedAt: _published, publishInterval: _interval, grace: _grace),
        _deadline,
      );
    });

    test('the boundary instant itself is still fresh', () {
      expect(_evaluate(deviceNow: _deadline), isA<CopyFresh>());
      expect(
        _evaluate(deviceNow: _deadline.add(const Duration(seconds: 1))),
        isA<CopyStale>(),
      );
    });
  });

  group('DESIGN section 9.1 rule 3: HOST_NOT_PUBLISHING iff all three conjuncts', () {
    final DateTime now = DateTime.utc(2026, 9, 17, 9);
    final DateTime afterDue = DateTime.utc(2026, 9, 17, 8);

    test('all three hold: the host is reachable and has published nothing', () {
      final state = _evaluate(deviceNow: now, diagnostics: _succeededAt(afterDue));
      final reason = (state as CopyStale).reason as HostNotPublishing;
      // The claim is dated at both ends: what we hold, and when that was last
      // confirmed. A claim about the host is worth what its confirmation is.
      expect(reason.lastPublishedAt, _published);
      expect(reason.confirmedAt, afterDue);
    });

    test('conjunct 1: a success before the deadline has not checked since it was due', () {
      // Reached the source, but *before* there was anything overdue to see, so
      // it cannot be evidence that the host has stopped.
      final state = _evaluate(
        deviceNow: now,
        continuity: trustedClock,
        diagnostics: _succeededAt(_deadline.subtract(const Duration(seconds: 1))),
      );
      expect(_cannotCheck(state), isA<NotCheckedSinceDue>());
      expect(((state as CopyStale).reason as CannotCheck).since,
          _deadline.subtract(const Duration(seconds: 1)));
    });

    test('conjunct 1: the deadline instant itself counts as after it', () {
      // `last_fetch_success_at >= stale_after` — the boundary belongs to the
      // host-blaming side, and one tick earlier does not.
      expect(
        ((_evaluate(deviceNow: now, diagnostics: _succeededAt(_deadline)) as CopyStale).reason),
        isA<HostNotPublishing>(),
      );
    });

    test('conjunct 2: a held error is the shape that says something failed since', () {
      for (final error in FetchFailureClass.values) {
        final state = _evaluate(
          deviceNow: now,
          continuity: trustedClock,
          diagnostics: _failedAt(now, error: error, afterSuccessAt: afterDue),
        );
        final cause = _cannotCheck(state) as FetchFailed;
        // The error class is carried, not flattened: `offline` and
        // `hostUnreachable` are opposite ends of how much the owner should care.
        expect(cause.errorClass, error);
        expect(((state as CopyStale).reason as CannotCheck).since, afterDue);
      }
    });

    test('conjunct 2: a phone that has attempted and never succeeded has no since', () {
      final state = _evaluate(deviceNow: now, diagnostics: _failedAt(now));
      expect((_cannotCheck(state) as FetchFailed).errorClass, FetchFailureClass.offline);
      expect(((state as CopyStale).reason as CannotCheck).since, isNull);
    });

    test('conjunct 3: what it served last is not what we hold', () {
      final state = _evaluate(
        deviceNow: now,
        continuity: trustedClock,
        diagnostics: _succeededAt(afterDue, seq: PublicationSeq.parse('42')),
      );
      expect(_cannotCheck(state), isA<ServedPayloadNotHeld>());
      expect(((state as CopyStale).reason as CannotCheck).since, afterDue);
    });

    test('conjunct 3 compares the counter as a number, not as its wire string', () {
      // `'9' > '10'` lexicographically; the tenth publication is where a string
      // comparison would first read equality wrong.
      final state = evaluateCopyState(
        publishedAt: _published,
        publishInterval: _interval,
        grace: _grace,
        deviceNow: now,
        continuity: trustedClock,
        diagnostics: _succeededAt(afterDue, seq: PublicationSeq.parse('10')),
        baseline: BaselineHeld(
          SeqBaseline(pairingId: _pairing, lastSeq: PublicationSeq.parse('10')),
        ),
      );
      expect((state as CopyStale).reason, isA<HostNotPublishing>());
    });

    test('never fetched is the absence of a record, and says so', () {
      final state = _evaluate(deviceNow: now);
      expect(_cannotCheck(state), isA<NeverFetched>());
      expect(((state as CopyStale).reason as CannotCheck).since, isNull);
    });

    test('damaged notes are never reported as never fetched', () {
      // A phone that fetched for a month and lost its notes has not "never
      // fetched" — that would be a claim with nothing behind it.
      final state = _evaluate(
        deviceNow: now,
        continuity: trustedClock,
        diagnostics: const DiagnosticsUnreadable('stored fetch diagnostics are not JSON'),
      );
      final cause = _cannotCheck(state) as RecordsUnusable;
      expect(cause.reason, 'stored fetch diagnostics are not JSON');
    });

    test('a copy held with no readable baseline cannot establish conjunct 3', () {
      // `last_seq` lives only in the baseline, so its two non-value cases are
      // answers here. Neither may pass for "it had nothing newer than we hold".
      for (final baseline in <BaselineState>[
        const BaselineAbsent(),
        const BaselineUnreadable('stored baseline is not JSON'),
      ]) {
        final state = _evaluate(
          deviceNow: now,
          continuity: trustedClock,
          diagnostics: _succeededAt(afterDue),
          baseline: baseline,
        );
        expect(_cannotCheck(state), isA<RecordsUnusable>());
      }
    });
  });

  group('the dimension alone', () {
    test('evaluateCopyFreshness delegates, so the two answers cannot drift', () {
      expect(
        evaluateCopyFreshness(
          publishedAt: _published,
          publishInterval: _interval,
          grace: _grace,
          deviceNow: DateTime.utc(2026, 9, 15, 12),
          continuity: trustedClock,
        ),
        CopyFreshness.fresh,
      );
      expect(
        evaluateCopyFreshness(
          publishedAt: _published,
          publishInterval: _interval,
          grace: _grace,
          deviceNow: DateTime.utc(2026, 9, 17),
          continuity: trustedClock,
        ),
        CopyFreshness.stale,
      );
      expect(
        evaluateCopyFreshness(
          publishedAt: _published,
          publishInterval: _interval,
          grace: _grace,
          deviceNow: _published.subtract(const Duration(hours: 1)),
          continuity: trustedClock,
        ),
        CopyFreshness.unknown,
      );
    });

    test('each state reports its own dimension', () {
      expect(const CopyFresh().freshness, CopyFreshness.fresh);
      expect(
        const CopyStale(CannotCheck(cause: NeverFetched())).freshness,
        CopyFreshness.stale,
      );
      expect(
        const CopyUnknown(ClockDisagreement.payloadFromTheFuture).freshness,
        CopyFreshness.unknown,
      );
    });
  });
}
