import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/copy_freshness.dart';

import 'fixtures.dart';

const Duration _interval = Duration(seconds: 86400);
const Duration _grace = Duration(seconds: 21600);
final DateTime _published = DateTime.utc(2026, 9, 15, 4);

CopyFreshness _at(DateTime deviceNow) => evaluateCopyFreshness(
      publishedAt: _published,
      publishInterval: _interval,
      grace: _grace,
      deviceNow: deviceNow,
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

    expect(payload.copyFreshness(DateTime.utc(2026, 9, 15, 12)), CopyFreshness.fresh);
    expect(payload.copyFreshness(DateTime.utc(2026, 9, 20)), CopyFreshness.stale);
  });
}
