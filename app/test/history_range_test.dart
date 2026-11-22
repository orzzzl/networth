import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/history_range.dart';
import 'package:networth_app/src/domain/money.dart';
import 'package:networth_app/src/domain/net_worth_history.dart';

import 'net_worth_history_test.dart' show point;

DateTime _day(int year, int month, int day) => DateTime.utc(year, month, day);

void main() {
  group('a range reaches back from the last reading', () {
    final last = _day(2026, 11, 21);

    test('by calendar months and years', () {
      expect(ChartRange.oneMonth.startFor(last), _day(2026, 10, 21));
      expect(ChartRange.threeMonths.startFor(last), _day(2026, 8, 21));
      expect(ChartRange.oneYear.startFor(last), _day(2025, 11, 21));
      expect(ChartRange.threeYears.startFor(last), _day(2023, 11, 21));
      expect(ChartRange.fiveYears.startFor(last), _day(2021, 11, 21));
    });

    test('year to date starts on January 1 of the last reading\'s year', () {
      expect(ChartRange.yearToDate.startFor(last), _day(2026, 1, 1));
      expect(ChartRange.yearToDate.startFor(_day(2027, 1, 1)), _day(2027, 1, 1));
    });

    test('a month too short for the day clamps to its last day', () {
      expect(ChartRange.oneMonth.startFor(_day(2026, 3, 31)), _day(2026, 2, 28));
      expect(ChartRange.threeMonths.startFor(_day(2026, 5, 31)), _day(2026, 2, 28));
      expect(ChartRange.oneMonth.startFor(_day(2028, 3, 31)), _day(2028, 2, 29));
    });

    test('5Y from 29 February is 28 February, where the host stops its series', () {
      // `publisher.py::_years_before` makes the same choice, so the longest
      // range shows exactly what the host sent and nothing it withheld.
      expect(ChartRange.fiveYears.startFor(_day(2028, 2, 29)), _day(2023, 2, 28));
    });
  });

  group('the change across a range', () {
    RangeChange? over(List<int> values) => RangeChange.over(
          NetWorthHistory.reduce([
            for (var i = 0; i < values.length; i++)
              point('2026-09-${(10 + i).toString().padLeft(2, '0')}T20:00:00Z', values[i]),
          ]),
        );

    test('is the last reading less the first, in the series\' currency', () {
      final change = over([10000, 99999, 12345])!;
      expect(change.change, const Money(minorUnits: 2345, currency: 'USD'));
      expect(change.from.total.amount.minorUnits, 10000);
      expect(change.to.total.amount.minorUnits, 12345);
    });

    test('a single reading has none', () {
      expect(over([10000]), isNull);
    });

    test('the percentage rounds half away from zero, in integers', () {
      expect(over([10000, 12345])!.percentHundredths, 2345);
      expect(over([30000, 30001])!.percentHundredths, 0); // 0.0033%
      expect(over([20000, 20001])!.percentHundredths, 1); // 0.005% → 0.01%
      expect(over([20000, 19999])!.percentHundredths, -1); // -0.005% → -0.01%
      expect(over([300, 100])!.percentHundredths, -6667);
    });

    test('there is no percentage of nothing or of a debt', () {
      expect(over([0, 500])!.percentHundredths, isNull);
      expect(over([-1000, 500])!.percentHundredths, isNull);
    });

    test('is written with its sign', () {
      expect(formatSignedMoney(const Money(minorUnits: 123456, currency: 'USD')), r'+$1,234.56');
      expect(formatSignedMoney(const Money(minorUnits: -8000, currency: 'USD')), r'-$80.00');
      expect(formatSignedMoney(const Money(minorUnits: 0, currency: 'USD')), r'$0.00');
      expect(formatSignedPercent(123), '+1.23%');
      expect(formatSignedPercent(-50), '-0.50%');
      expect(formatSignedPercent(0), '0.00%');
      expect(formatSignedPercent(-6667), '-66.67%');
    });
  });

  group('a range holds the readings inside it and nothing else', () {
    final series = NetWorthHistory.reduce([
      point('2026-11-12T20:00:00Z', 1),
      point('2026-11-13T20:00:00Z', 2),
      point('2026-11-21T20:00:00Z', 3),
    ]);

    test('one longer than the record starts at the first reading, unpadded', () {
      final range = series.since(ChartRange.fiveYears.startFor(series.points.last.day));
      expect(range.points, series.points);
    });

    test('one shorter than the record drops what is older', () {
      final range = series.since(_day(2026, 11, 13));
      expect([for (final p in range.points) p.total.amount.minorUnits], [2, 3]);
      expect(range.hasGap, isTrue, reason: 'the missing days are still missing');
    });
  });

  group('the time axis is labelled at calendar boundaries', () {
    test('a single day once', () {
      expect(axisTicks(_day(2026, 11, 21), _day(2026, 11, 21)), [
        AxisTick(_day(2026, 11, 21), AxisUnit.day),
      ]);
    });

    test('a month by days, a quarter by months, a year by quarters', () {
      final month = axisTicks(_day(2026, 10, 21), _day(2026, 11, 21));
      expect(month.map((t) => t.unit).toSet(), {AxisUnit.day});
      expect(month.first.day, _day(2026, 10, 21));

      expect(axisTicks(_day(2026, 8, 21), _day(2026, 11, 21)), [
        AxisTick(_day(2026, 9, 1), AxisUnit.month),
        AxisTick(_day(2026, 10, 1), AxisUnit.month),
        AxisTick(_day(2026, 11, 1), AxisUnit.month),
      ]);

      // January is the year turning, and is labelled as one.
      expect(axisTicks(_day(2025, 11, 21), _day(2026, 11, 21)), [
        AxisTick(_day(2026, 1, 1), AxisUnit.year),
        AxisTick(_day(2026, 4, 1), AxisUnit.month),
        AxisTick(_day(2026, 7, 1), AxisUnit.month),
        AxisTick(_day(2026, 10, 1), AxisUnit.month),
      ]);
    });

    test('several years by years', () {
      expect(axisTicks(_day(2021, 11, 21), _day(2026, 11, 21)), [
        for (final year in [2022, 2023, 2024, 2025, 2026]) AxisTick(_day(year, 1, 1), AxisUnit.year),
      ]);
    });

    test('never more marks than fit, whatever the span', () {
      final last = _day(2026, 11, 21);
      for (var days = 1; days < 2000; days += 7) {
        final ticks = axisTicks(last.subtract(Duration(days: days)), last);
        expect(ticks.length, inInclusiveRange(1, 5), reason: '$days days');
      }
    });
  });
}
