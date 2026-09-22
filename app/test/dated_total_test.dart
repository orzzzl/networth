import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/dated_total.dart';
import 'package:networth_app/src/domain/payload_format_exception.dart';

import 'fixtures.dart';

Map<String, Object?> _total(String assetPath) =>
    (jsonDecode(readFixture(assetPath)) as Map<String, Object?>)['total']! as Map<String, Object?>;

Map<String, Object?> _totalWith(String assetPath, Map<String, Object?> overrides) =>
    {..._total(assetPath), ...overrides};

void main() {
  group('the three age states of DESIGN section 8.1 R3', () {
    test('KNOWN carries the oldest source clock', () {
      final total = DatedTotal.fromJson(_total(knownFixture));

      expect(total, isA<KnownAgeTotal>());
      expect(
        (total as KnownAgeTotal).asOf,
        DateTime.utc(2026, 9, 14, 20, 15),
      );
      expect(total.amount.minorUnits, 4250000);
      expect(total.amount.currency, 'USD');
    });

    test('UNKNOWN carries the counts and, by construction, no date at all', () {
      final total = DatedTotal.fromJson(_total(mixedFixture));

      expect(total, isA<UndatableTotal>());
      final undatable = total as UndatableTotal;
      expect(undatable.undatableAccountCount, 1);
      expect(undatable.accountCount, 3);

      // The fixture *does* carry `oldest_known_source_as_of`, which is what makes
      // it the interesting case: R3 keeps that field as a diagnostic and forbids
      // presenting it as the total's age. The guarantee here is structural —
      // there is no field on this type for it to have been copied into.
      expect(_total(mixedFixture)['oldest_known_source_as_of'], isNotNull);
      expect(
        undatable.toString(),
        isNot(contains('2026-09-14')),
        reason: 'the diagnostic date must not reach the object the UI renders',
      );
    });

    test('STATIC_ONLY is a real state and parses as one', () {
      final total = DatedTotal.fromJson(_total(staticOnlyFixture));

      expect(total, isA<StaticOnlyTotal>());
      expect(total.staticAccountCount, 1);
    });
  });

  group('a payload that disagrees with R3 is refused, never rendered', () {
    test('KNOWN without a date', () {
      expect(
        () => DatedTotal.fromJson(_totalWith(knownFixture, {'as_of': null})),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('UNKNOWN that smuggles a date in', () {
      expect(
        () => DatedTotal.fromJson(
          _totalWith(mixedFixture, {'as_of': '2026-09-14T20:15:00.000000Z'}),
        ),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('STATIC_ONLY that smuggles a date in', () {
      expect(
        () => DatedTotal.fromJson(
          _totalWith(staticOnlyFixture, {'as_of': '2026-09-14T20:15:00.000000Z'}),
        ),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('an age state this build has never heard of', () {
      expect(
        () => DatedTotal.fromJson(_totalWith(knownFixture, {'age_state': 'PROBABLY_FINE'})),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('a total whose amount is not an integer', () {
      expect(
        () => DatedTotal.fromJson(_totalWith(knownFixture, {'value_minor': 42500.0})),
        throwsA(isA<PayloadFormatException>()),
      );
    });
  });

  group('written back for the phone\'s own record (task 23a)', () {
    // The store keeps readings in this shape and reads them back with the
    // parser above, so `toJson` being the exact inverse of `fromJson` is what
    // makes a recorded reading a readable one. A round trip is the test: it
    // fails on a field dropped, renamed, or spelled differently in one
    // direction, which no assertion about a single key would catch.
    for (final (name, fixture) in [
      ('KNOWN', knownFixture),
      ('UNKNOWN', mixedFixture),
      ('STATIC_ONLY', staticOnlyFixture),
    ]) {
      test('$name survives a round trip unchanged', () {
        final written = DatedTotal.fromJson(_total(fixture)).toJson();

        expect(DatedTotal.fromJson(written).toJson(), written);
        expect(written['age_state'], name);
      });
    }

    test('it carries the published numbers, not a re-rendering of them', () {
      final written = DatedTotal.fromJson(_total(knownFixture)).toJson();

      expect(written['value_minor'], 4250000);
      expect(written['assets_minor'], 5000000);
      expect(written['liabilities_minor'], 750000);
      expect(written['currency'], 'USD');
      expect(written['is_complete'], true);
      expect(written['as_of'], '2026-09-14T20:15:00.000Z');
    });

    test('an undatable total writes no as_of at all — absent, not null', () {
      // §8.1 R3 forbids the two undatable states a date, and `fromJson` refuses
      // a total that carries one. A `null` would round-trip today and become a
      // refusal the moment anything wrote it as a string, so the key is absent.
      final written = DatedTotal.fromJson(_total(mixedFixture)).toJson();

      expect(written.containsKey('as_of'), isFalse);
      expect(written['unknown_freshness_account_count'], isA<int>());
      expect(written['account_count'], isA<int>());
    });
  });
}
