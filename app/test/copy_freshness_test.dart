import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/clock_continuity.dart';
import 'package:networth_app/src/domain/copy_freshness.dart';
import 'package:networth_app/src/domain/fetch_diagnostics.dart';
import 'package:networth_app/src/domain/publication_seq.dart';
import 'package:networth_app/src/domain/seq_baseline.dart';

import 'fixtures.dart';

/// A clock this device has proved continuous: the same interval measured two
/// ways, agreeing exactly. The neutral value for tests about *age*, which is a
/// different question from whether the clock can be trusted at all.
const ClockContinuity trustedClock =
    ContinuityHeld(wallElapsed: Duration.zero, monotonicElapsed: Duration.zero);

const Duration _interval = Duration(seconds: 86400);
const Duration _grace = Duration(seconds: 21600);
final DateTime _published = DateTime.utc(2026, 9, 15, 4);

CopyFreshness _at(DateTime deviceNow) => evaluateCopyFreshness(
      publishedAt: _published,
      publishInterval: _interval,
      grace: _grace,
      deviceNow: deviceNow,
      continuity: trustedClock,
    );

void main() {
  group('DESIGN section 9.1: when this phone copy is stale', () {
    test('the deadline travels with the payload rather than being hardcoded', () {
      expect(
        staleAfter(publishedAt: _published, publishInterval: _interval, grace: _grace),
        DateTime.utc(2026, 9, 16, 10),
      );
    });

    test('inside the window is fresh', () {
      expect(_at(DateTime.utc(2026, 9, 15, 12)), CopyFreshness.fresh);
    });

    test('the boundary instant itself is still fresh', () {
      // Rule 2 is `device_now <= stale_after`, so this is the off-by-one that
      // decides whether the app cries wolf exactly on time or one tick early.
      expect(_at(DateTime.utc(2026, 9, 16, 10)), CopyFreshness.fresh);
      expect(
        _at(DateTime.utc(2026, 9, 16, 10, 0, 1)),
        CopyFreshness.stale,
      );
    });

    test('past the window is stale', () {
      expect(_at(DateTime.utc(2026, 9, 17)), CopyFreshness.stale);
    });

    test('a payload from the future means the age cannot be computed', () {
      expect(_at(DateTime.utc(2026, 9, 15, 3, 50)), CopyFreshness.unknown);
    });

    test('a small skew is tolerated rather than reported as a clock fault', () {
      // Five minutes of tolerance, so ordinary NTP drift does not present as a
      // broken device clock.
      expect(_at(DateTime.utc(2026, 9, 15, 3, 56)), CopyFreshness.fresh);
    });
  });

  group('the connection dimension arrives already decided', () {
    test('each wire value maps to one state', () {
      expect(ConnectionDisplayState.fromWire('OK'), ConnectionDisplayState.ok);
      expect(ConnectionDisplayState.fromWire('WAITING'), ConnectionDisplayState.waiting);
      expect(ConnectionDisplayState.fromWire('ACTION_NEEDED'), ConnectionDisplayState.actionNeeded);
    });

    test('an unknown value is refused rather than defaulted to OK', () {
      expect(() => ConnectionDisplayState.fromWire('FINE'), throwsArgumentError);
    });
  });

  test('the shipped fixtures evaluate against a chosen instant', () {
    final payload = loadFixture(knownFixture);

    CopyFreshness at(DateTime deviceNow) => payload.copyFreshness(
          deviceNow,
          continuity: trustedClock,
          diagnostics: noFetchRecords,
          baseline: noSeqBaseline,
        );

    expect(at(DateTime.utc(2026, 9, 15, 12)), CopyFreshness.fresh);
    expect(at(DateTime.utc(2026, 9, 20)), CopyFreshness.stale);
  });

  group('the records reach the predicate through copyState', () {
    // **The test the structural change needs, because the change itself is
    // invisible to a mutation run.** Making `diagnostics` and `baseline`
    // required is enforced by the analyzer, so reverting the signature does not
    // fail a test — it fails to compile, and nothing in a green suite says so.
    //
    // What a test *can* pin is the behaviour the parameters exist to make
    // reachable: a caller's records actually deciding the verdict. The mutation
    // this is aimed at is the quiet one — keep the parameters, ignore them, and
    // hardcode absence inside `copyState` again, exactly as it was. That
    // compiles, and it silently restores a screen that can never report
    // HOST_NOT_PUBLISHING.
    final payload = loadFixture(alertsOpenFixture);
    // Past `published_at` + interval + grace, so the copy is stale and the
    // reason is the only thing left to decide.
    final deviceNow = DateTime.utc(2026, 9, 17, 12);
    final seq = PublicationSeq.parse(payload.seq);

    test('held records naming the copy report HOST_NOT_PUBLISHING', () {
      // All three conjuncts hold: the last attempt succeeded (so attempt and
      // success coincide by construction), it is at or after the copy went
      // stale, and the seq it fetched is the baseline the phone holds.
      final copy = payload.copyState(
        deviceNow,
        continuity: trustedClock,
        diagnostics: DiagnosticsHeld(
          FetchDiagnostics.succeeded(
            pairingId: payload.pairingId,
            at: deviceNow.subtract(const Duration(minutes: 5)),
            seq: seq,
          ),
        ),
        baseline: BaselineHeld(
          SeqBaseline(pairingId: payload.pairingId, lastSeq: seq),
        ),
      );

      expect(copy, isA<CopyStale>());
      expect((copy as CopyStale).reason, isA<HostNotPublishing>());
    });

    test('absent records over the same copy can only say CANNOT_CHECK', () {
      // The control, and it is what makes the assertion above mean something:
      // same payload, same instant, same clock — only the records differ. If
      // `copyState` ignored its arguments both cases would land here, so this
      // pair is what tells "the records are read" from "the records are named".
      final copy = payload.copyState(
        deviceNow,
        continuity: trustedClock,
        diagnostics: noFetchRecords,
        baseline: noSeqBaseline,
      );

      expect(copy, isA<CopyStale>());
      expect((copy as CopyStale).reason, isA<CannotCheck>());
    });
  });
}
