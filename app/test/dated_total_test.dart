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
}
