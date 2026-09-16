import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/instant.dart';
import 'package:networth_app/src/domain/payload_format_exception.dart';

/// PR #77 review blocker 3.
///
/// A timestamp without a zone is not a timestamp: Dart parses it as *device-local*
/// time, so the same bytes become a different instant on every phone. That moves
/// the displayed source date and it moves the copy-staleness decision, which
/// compares `published_at` against the device clock — so a zone-less string could
/// make one phone call a copy fresh and another call it overdue, from one payload.
///
/// **Every test here is device-timezone-independent on purpose.** They assert
/// refusals, or they assert the UTC value of a string that carries its own zone;
/// neither depends on where the machine running them is. A test that asserted
/// "this string becomes 11:00Z" would pass in California and fail in Tokyo, which
/// is the very confusion under test.
void main() {
  group('a wire instant must carry a zone', () {
    test('a bare date-time is refused rather than read as device-local', () {
      expect(
        () => parseWireInstant('2026-09-15T04:00:00', field: 'published_at'),
        throwsA(
          isA<PayloadFormatException>().having(
            (e) => e.message,
            'message',
            contains('no timezone'),
          ),
        ),
      );
    });

    test('and so are the other zone-less shapes Dart accepts', () {
      // Enumerated because `DateTime.tryParse` is more permissive than the ISO
      // subset the daemon emits, and each of these parses happily.
      for (final value in [
        '2026-09-15',
        '2026-09-15 04:00:00',
        '2026-09-15T04:00:00.123456',
        '2026-09-15T04:00',
      ]) {
        expect(
          () => parseWireInstant(value, field: 'published_at'),
          throwsA(isA<PayloadFormatException>()),
          reason: '"$value" carries no zone and was accepted',
        );
      }
    });

    test('text that is not a timestamp at all is still refused', () {
      expect(
        () => parseWireInstant('not-a-time', field: 'published_at'),
        throwsA(
          isA<PayloadFormatException>().having(
            (e) => e.message,
            'message',
            contains('not a timestamp'),
          ),
        ),
      );
    });

    test('Z, z and an explicit offset are all accepted and normalised to UTC', () {
      // The accepting half, and the one that pins the library behaviour the
      // refusal rests on: `isUtc` is true when and only when the input carried a
      // zone designator. If a future Dart changed that, this goes red here
      // rather than silently reopening the hole above.
      final expected = DateTime.utc(2026, 9, 15, 4);
      for (final value in [
        '2026-09-15T04:00:00Z',
        '2026-09-15T04:00:00z',
        '2026-09-15T04:00:00+00:00',
        '2026-09-15T12:00:00+08:00',
        '2026-09-14T21:00:00-07:00',
      ]) {
        final parsed = parseWireInstant(value, field: 'published_at');
        expect(parsed.isUtc, isTrue, reason: value);
        expect(parsed, expected, reason: value);
      }
    });

    test('the refusal names the field, because the payload has several', () {
      expect(
        () => parseWireInstant('2026-09-15T04:00:00', field: 'total.as_of'),
        throwsA(
          isA<PayloadFormatException>().having(
            (e) => e.message,
            'message',
            startsWith('total.as_of'),
          ),
        ),
      );
    });
  });
}
