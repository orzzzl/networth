import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/clock_anchor_store.dart';
import 'package:networth_app/src/data/clock_continuity_source.dart';
import 'package:networth_app/src/domain/clock_anchor.dart';
import 'package:networth_app/src/domain/clock_continuity.dart';
import 'package:networth_app/src/domain/copy_freshness.dart';
import 'package:networth_app/src/domain/fetch_diagnostics.dart';
import 'package:networth_app/src/domain/publication_seq.dart';
import 'package:networth_app/src/domain/seq_baseline.dart';

/// A monotonic source under the test's control, standing in for the platform
/// channel task `22` still owes.
class _FakeSource implements ClockContinuitySource {
  _FakeSource({this.reading, this.error, this.onRead});

  MonotonicReading? reading;
  Object? error;

  /// Runs before the reading is returned, so a test can land a fetch in the
  /// window between the two reads.
  final Future<void> Function()? onRead;

  @override
  Future<MonotonicReading?> read() async {
    await onRead?.call();
    final thrown = error;
    if (thrown != null) {
      throw thrown;
    }
    return reading;
  }
}

void main() {
  late Directory directory;
  late File file;
  late FileClockAnchorStore anchors;

  setUp(() async {
    directory = await Directory.systemTemp.createTemp('clock-evidence-test');
    file = File('${directory.path}/${FileClockAnchorStore.fileName}');
    anchors = FileClockAnchorStore(open: () async => file);
  });

  tearDown(() async => directory.delete(recursive: true));

  const pairing = 'pairing-a';
  const publishInterval = Duration(hours: 6);
  const grace = Duration(hours: 6);

  /// The copy this phone holds throughout: published Sep 1, so its deadline is
  /// Sep 2 00:00 and anything later is stale.
  final publishedAt = DateTime.utc(2026, 9, 1, 12);

  /// **Production semantics**: the app cannot corroborate a stamp, so what it
  /// offers is always [AnchorTrust.unproven] and what it gets back is whatever
  /// the store decides to carry forward.
  Future<void> anchorAt({
    required DateTime at,
    required String runId,
    Duration elapsed = Duration.zero,
    String seq = '41',
    AnchorTrust trust = AnchorTrust.unproven,
  }) =>
      anchors.establish(
        ClockAnchor(
          pairingId: pairing,
          anchoredAt: at,
          reading: MonotonicReading(runId: runId, elapsed: elapsed),
          seq: PublicationSeq.parse(seq),
          trust: trust,
        ),
      );

  /// Put a **corroborated** anchor in place — a capability no caller in the app
  /// has today, and named loudly for that reason.
  ///
  /// Most cases below are about what happens to a clock *after* its anchor was
  /// worth something; without this they would all collapse into
  /// [ContinuityGap.anchorUnproven] and stop distinguishing anything. It is a
  /// separate name rather than a default because a helper that quietly mints
  /// trust is how a test proves a thing the product cannot do — and on the
  /// advance path that is precisely the defect this group exists to pin.
  Future<void> corroborateAt({
    required DateTime at,
    required String runId,
    Duration elapsed = Duration.zero,
    String seq = '41',
  }) =>
      anchorAt(
        at: at,
        runId: runId,
        elapsed: elapsed,
        seq: seq,
        trust: AnchorTrust.corroborated,
      );

  Future<ClockEvidence> observe(_FakeSource source) =>
      ClockEvidenceReader(anchors: anchors, source: source).observe(pairing);

  /// The production order: observe the records, **then** read the wall clock,
  /// then evaluate. [deviceNow] stands in for that reading.
  Future<CopyState> verdict({
    required _FakeSource source,
    required DateTime deviceNow,
    DateTime? published,
    DiagnosticsState diagnostics = const DiagnosticsAbsent(),
    BaselineState baseline = const BaselineAbsent(),
  }) async {
    final evidence = await observe(source);
    return evaluateCopyState(
      publishedAt: published ?? publishedAt,
      publishInterval: publishInterval,
      grace: grace,
      deviceNow: deviceNow,
      continuity: evidence.at(deviceNow),
      diagnostics: diagnostics,
      baseline: baseline,
    );
  }

  ClockDisagreement unknown(CopyState state) {
    expect(state, isA<CopyUnknown>(), reason: 'got $state');
    return (state as CopyUnknown).disagreement;
  }

  ContinuityGap gap(ClockEvidence evidence) {
    expect(evidence, isA<NoClockEvidence>(), reason: 'got $evidence');
    return (evidence as NoClockEvidence).gap;
  }

  group('case 1 — nine days offline, then the clock is moved back', () {
    test('the copy is not fresh, and the deadline alone would have said it was', () async {
      await anchorAt(at: publishedAt, runId: 'boot-1');
      // Nine days pass with the app closed. The process is gone, so the source
      // cannot identify the run the anchor came from.
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-2', elapsed: Duration.zero),
      );
      final deviceNow = DateTime.utc(2026, 9, 1, 13);

      // The precondition, asserted rather than assumed: with the clock moved
      // back, §9.1 rule 2 on its own puts this copy comfortably inside its
      // deadline. Continuity is the only thing standing between a nine-day-old
      // copy and a confident "fresh".
      expect(
        staleAfter(publishedAt: publishedAt, publishInterval: publishInterval, grace: grace)
            .isAfter(deviceNow),
        isTrue,
      );

      expect(
        unknown(await verdict(source: source, deviceNow: deviceNow)),
        ClockDisagreement.clockContinuityUnknown,
      );
    });

    test('and when the process survived, the drift itself is the evidence', () async {
      // The same rollback with the app never killed: the source counts the nine
      // days, the wall clock claims one hour, and the disagreement is measured
      // rather than merely unproven.
      await corroborateAt(at: publishedAt, runId: 'boot-1');
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(days: 9)),
      );
      expect(
        unknown(await verdict(source: source, deviceNow: DateTime.utc(2026, 9, 1, 13))),
        ClockDisagreement.deviceClockMovedBackwards,
      );
    });
  });

  group('case 2 — a reachable host whose publisher has stopped', () {
    test('a successful fetch of the publication we hold cannot re-anchor', () async {
      // Codex's counterexample, and the one that killed the "the host is a
      // second clock" argument: `published_at` is the host's clock at
      // publication, not at this response, so a frozen publisher corroborates
      // nothing — least of all in the case `HOST_NOT_PUBLISHING` exists for.
      await anchorAt(at: publishedAt, runId: 'boot-1', seq: '41');
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-2', elapsed: Duration.zero),
      );
      final deviceNow = DateTime.utc(2026, 9, 1, 13);

      await expectLater(
        anchorAt(at: deviceNow, runId: 'boot-2', seq: '41'),
        throwsA(isA<AnchorNotAdvanced>()),
      );

      expect(
        unknown(await verdict(source: source, deviceNow: deviceNow)),
        ClockDisagreement.clockContinuityUnknown,
      );
    });
  });

  test('case 3 — an unchanged clock and a young copy is fresh', () async {
    final published = DateTime.utc(2026, 9, 20, 9);
    await corroborateAt(at: published, runId: 'boot-1', elapsed: const Duration(hours: 1));
    final source = _FakeSource(
      reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(hours: 2)),
    );
    expect(
      await verdict(
        source: source,
        published: published,
        deviceNow: DateTime.utc(2026, 9, 20, 10),
      ),
      isA<CopyFresh>(),
    );
  });

  test('case 4 — days asleep still age the copy', () async {
    // The obligation on the source, from the other side: it counts suspended
    // time, so nine days of sleep are nine days of drift-free ageing and the
    // copy is stale rather than unknown. A source that stopped while the device
    // slept would report a nine-day *drift* here and hide a stale copy behind
    // an unknown clock.
    await corroborateAt(at: publishedAt, runId: 'boot-1');
    final source = _FakeSource(
      reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(days: 9)),
    );
    final state = await verdict(source: source, deviceNow: DateTime.utc(2026, 9, 10, 12));
    expect(state, isA<CopyStale>());
    expect((state as CopyStale).reason, isA<CannotCheck>());
  });

  group('case 5 — missing or corrupt evidence is never a good clock', () {
    test('no anchor yet', () async {
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration.zero),
      );
      expect(gap(await observe(source)), ContinuityGap.noAnchor);
      expect(
        unknown(await verdict(source: source, deviceNow: DateTime.utc(2026, 9, 1, 13))),
        ClockDisagreement.clockContinuityUnknown,
      );
    });

    test('an anchor that will not parse', () async {
      await file.writeAsString('{not json');
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration.zero),
      );
      expect(gap(await observe(source)), ContinuityGap.anchorUnreadable);
      expect(
        unknown(await verdict(source: source, deviceNow: DateTime.utc(2026, 9, 1, 13))),
        ClockDisagreement.clockContinuityUnknown,
      );
    });

    test('a source that cannot answer', () async {
      await corroborateAt(at: publishedAt, runId: 'boot-1');
      expect(gap(await observe(_FakeSource())), ContinuityGap.sourceUnavailable);
    });

    test('a source that throws something this layer has never heard of', () async {
      await corroborateAt(at: publishedAt, runId: 'boot-1');
      expect(
        gap(await observe(_FakeSource(error: 'a platform channel error'))),
        ContinuityGap.sourceUnavailable,
      );
    });

    test('the source that ships today answers nothing at all', () async {
      // Deliberate: no monotonic source has landed, so the honest verdict on
      // every copy is unknown rather than fresh.
      await corroborateAt(at: publishedAt, runId: 'boot-1');
      final reader = ClockEvidenceReader(
        anchors: anchors,
        source: const UnavailableClockContinuitySource(),
      );
      expect(gap(await reader.observe(pairing)), ContinuityGap.sourceUnavailable);
    });
  });

  group('case 6 — a bigger number is not proof of the same run', () {
    test('a different run is discontinuous even when its reading is larger', () async {
      await corroborateAt(
        at: publishedAt,
        runId: 'boot-1',
        elapsed: const Duration(seconds: 10),
      );
      const now = MonotonicReading(runId: 'boot-2', elapsed: Duration(days: 3));
      // Asserted rather than assumed: the naive "it went up, so it is the same
      // counter" test passes here, which is the whole reason the run id exists.
      expect(now.elapsed > const Duration(seconds: 10), isTrue);
      expect(gap(await observe(_FakeSource(reading: now))), ContinuityGap.discontinuous);
    });

    test('the same run reading backwards is discontinuous, not negative drift', () async {
      await corroborateAt(at: publishedAt, runId: 'boot-1', elapsed: const Duration(hours: 5));
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(hours: 4)),
      );
      expect(gap(await observe(source)), ContinuityGap.discontinuous);
    });
  });

  test('the anchor is read before the live reading, not after', () async {
    // A fetch landing between the two reads is an ordinary race, and reading
    // them the other way round turns it into a fault report: the new anchor
    // paired with a reading taken before it gives a negative monotonic delta,
    // which this reader classifies as a broken source. So the order is checked
    // here rather than trusted, by landing exactly that fetch.
    await corroborateAt(at: publishedAt, runId: 'boot-1', seq: '41');
    final source = _FakeSource(
      reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(days: 9)),
      onRead: () => anchorAt(
        at: DateTime.utc(2026, 9, 10, 12),
        runId: 'boot-1',
        elapsed: const Duration(days: 9),
        seq: '42',
      ),
    );

    final evidence = await observe(source);
    expect(evidence, isA<AnchoredClockEvidence>(), reason: 'got $evidence');
    final anchored = evidence as AnchoredClockEvidence;
    expect(anchored.anchoredAt, publishedAt);
    expect(anchored.monotonicElapsed, const Duration(days: 9));
  });

  group('case 7 — a success cannot wash out an earlier disagreement', () {
    test('an accepted newer publication re-anchors without minting continuity', () async {
      // **This test used to assert the opposite, and the argument it was
      // pinning was wrong.** It said re-anchoring on an advance was sound
      // *because* §9.1 rule 1's first branch corroborates the device clock
      // against the host's publication instant. It does not: the future check
      // fires on `published_at > device_now + 5min`, so it only ever catches a
      // clock that is **behind** the publication, and a host that published
      // once and stopped serves a higher `seq` that is arbitrarily old. Review
      // reproduced the other direction against the real store — see case 8.
      await corroborateAt(at: publishedAt, runId: 'boot-1', seq: '41');
      final deviceNow = DateTime.utc(2026, 9, 1, 13);
      // The host is publishing fine; its clock says Sep 10, this phone's says
      // Sep 1. The fetch advances the seq, so the anchor is allowed to move.
      final published = DateTime.utc(2026, 9, 10, 13);
      await anchorAt(at: deviceNow, runId: 'boot-2', seq: '42');

      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-2', elapsed: Duration(minutes: 5)),
      );
      final later = deviceNow.add(const Duration(minutes: 5));
      // The new anchor and the new reading agree perfectly with each other, and
      // that agreement is worth nothing: the process restarted, so the interval
      // from the corroborated anchor to this one was never measured.
      expect(gap(await observe(source)), ContinuityGap.anchorUnproven);

      expect(
        unknown(
          await verdict(source: source, published: published, deviceNow: later),
        ),
        // Rule 1's first branch is evaluated **before** continuity, so the more
        // specific fault gets the sentence. Reorder them and the owner is told
        // "couldn't tell" about a disagreement the phone can actually name.
        ClockDisagreement.payloadFromTheFuture,
      );
    });

    test('and the future check is not an artifact of an unproven anchor', () async {
      // The ordering above would also hold if every verdict were unknown, which
      // is a thing this component could easily have become. Same check, from a
      // corroborated anchor whose continuity is held and trustworthy.
      await corroborateAt(at: publishedAt, runId: 'boot-1', seq: '41');
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(hours: 1)),
      );
      final deviceNow = publishedAt.add(const Duration(hours: 1));
      final evidence = await observe(source);
      expect(evidence.at(deviceNow), isA<ContinuityHeld>());
      expect((evidence.at(deviceNow) as ContinuityHeld).isTrustworthy, isTrue);

      expect(
        unknown(
          await verdict(
            source: source,
            published: DateTime.utc(2026, 9, 10, 13),
            deviceNow: deviceNow,
          ),
        ),
        ClockDisagreement.payloadFromTheFuture,
      );
    });

    test('a refused re-anchor leaves the earlier anchor intact on disk', () async {
      await anchorAt(at: publishedAt, runId: 'boot-1', seq: '41');
      await expectLater(
        anchorAt(at: DateTime.utc(2026, 9, 10, 13), runId: 'boot-2', seq: '41'),
        throwsA(isA<AnchorNotAdvanced>()),
      );
      final state = await anchors.read(pairing);
      expect(state, isA<AnchorHeld>());
      expect((state as AnchorHeld).anchor.anchoredAt, publishedAt);
      expect(state.anchor.reading.runId, 'boot-1');
    });
  });

  group('case 8 — an advance is not a new clock, and a first anchor is not proof', () {
    // Raised in review of this component: refusing to move the anchor onto a
    // copy we already hold closed one hole, and the *advance* path had the same
    // one. All three cases below rendered `COPY_FRESH` before `AnchorTrust`
    // existed. They run through the real file store, the real reader and the
    // real predicate — the defect was invisible to every unit that had only its
    // own half.

    /// The host published this before it stopped. Thirty minutes before the
    /// phone's rolled-back wall clock, and nine days before the real instant.
    final frozen = DateTime.utc(2026, 9, 1, 12, 30);

    /// Sep 1 by this phone's clock; Sep 10 in fact.
    final rolledBack = DateTime.utc(2026, 9, 1, 13);

    test('a higher but frozen seq does not erase a measured nine-day rollback', () async {
      await corroborateAt(at: publishedAt, runId: 'boot-1', seq: '41');
      // Nine days suspended, and the clock corrected backwards. The source is
      // continuous and correct: one hour of wall time against nine days of real
      // time, which the phone correctly reports as its own fault.
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(days: 9)),
      );
      expect(
        unknown(await verdict(source: source, deviceNow: rolledBack)),
        ClockDisagreement.deviceClockMovedBackwards,
      );

      // Now a fetch returns seq 42 — newer than what we hold, and published
      // before the host stopped. The anchor is allowed to move, and the pair of
      // readings it would be stamped with agree perfectly with each other.
      await anchorAt(at: rolledBack, runId: 'boot-1', elapsed: const Duration(days: 9), seq: '42');

      expect(gap(await observe(source)), ContinuityGap.anchorUnproven);
      expect(
        unknown(await verdict(source: source, published: frozen, deviceNow: rolledBack)),
        // The kind of not-knowing changed and the answer did not: the phone no
        // longer holds a *measurement* of the rollback, it holds an anchor
        // nothing corroborated. Rendering this as fresh is the whole defect.
        ClockDisagreement.clockContinuityUnknown,
      );
    });

    for (final prior in <String>['no anchor at all', 'a damaged anchor']) {
      test('$prior cannot bootstrap a good clock from an old publication', () async {
        if (prior == 'a damaged anchor') {
          await file.writeAsString('{not json');
        }
        final source = _FakeSource(
          reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(days: 9)),
        );
        expect(await verdict(source: source, published: frozen, deviceNow: rolledBack),
            isA<CopyUnknown>());

        // The first accepted publication establishes the anchor. Measuring zero
        // drift since an arbitrary initial reading proves nothing about that
        // reading — which is why establishment starts unproven rather than
        // starting a trusted interval.
        await anchorAt(
          at: rolledBack,
          runId: 'boot-1',
          elapsed: const Duration(days: 9),
          seq: '42',
          trust: AnchorTrust.unproven,
        );

        expect(gap(await observe(source)), ContinuityGap.anchorUnproven);
        expect(
          unknown(await verdict(source: source, published: frozen, deviceNow: rolledBack)),
          ClockDisagreement.clockContinuityUnknown,
        );
      });
    }

    test('the control: a corroborated anchor on a healthy clock still reads fresh', () async {
      // Without this, every assertion above would be satisfied by a component
      // that answers `COPY_UNKNOWN` to everything — which is a thing this
      // change could easily have shipped, since the app cannot construct
      // `AnchorTrust.corroborated` at all today.
      await corroborateAt(at: publishedAt, runId: 'boot-1', seq: '41');
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(hours: 1)),
      );
      final deviceNow = publishedAt.add(const Duration(hours: 1));
      expect(await verdict(source: source, deviceNow: deviceNow), isA<CopyFresh>());

      // And it survives an advance that corroborates itself, so the rule is not
      // write-only. **The advance has to carry `corroborated`**, which is the
      // repair to the finding on this component: an advance used to inherit the
      // stored trust whenever the interval since the previous anchor held, and
      // because `establish` then replaced that anchor, the allowance was handed
      // out afresh at every publication instead of bounding error since the last
      // real corroboration. Small drifts accumulated unseen.
      await anchorAt(
        at: deviceNow,
        runId: 'boot-1',
        elapsed: const Duration(hours: 1),
        seq: '42',
        trust: AnchorTrust.corroborated,
      );
      expect(
        await verdict(source: source, published: deviceNow, deviceNow: deviceNow),
        isA<CopyFresh>(),
      );

      // The same advance without that proof is `COPY_UNKNOWN`, and this is the
      // half of the control that would have caught the finding: it is exactly
      // the step the old rule waved through.
      await anchorAt(
        at: deviceNow,
        runId: 'boot-1',
        elapsed: const Duration(hours: 1),
        seq: '43',
      );
      expect(
        await verdict(source: source, published: deviceNow, deviceNow: deviceNow),
        isA<CopyUnknown>(),
      );
    });
  });
}
