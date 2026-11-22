import 'package:flutter/foundation.dart';

import 'money.dart';
import 'net_worth_history.dart';

/// The chart's ranges, stock-app style.
///
/// Every range ends on the series' **last day**, not on the device's today: the
/// curve describes the copy on screen, whose own age is the copy row's business
/// (§9.1), and a range that moved with the device clock would let a wrong clock
/// redraw the past.
enum ChartRange {
  oneMonth,
  threeMonths,
  yearToDate,
  oneYear,
  threeYears,
  fiveYears;

  /// The first day this range reaches back to, for a series ending on [lastDay].
  ///
  /// Calendar months, clamped to the shorter month — 31 March less a month is
  /// 28 or 29 February, not 3 March — so 5Y lands on the day
  /// `publisher.py::_years_before` stops the host's series at.
  DateTime startFor(DateTime lastDay) => switch (this) {
        oneMonth => _monthsBefore(lastDay, 1),
        threeMonths => _monthsBefore(lastDay, 3),
        yearToDate => DateTime.utc(lastDay.year),
        oneYear => _monthsBefore(lastDay, 12),
        threeYears => _monthsBefore(lastDay, 36),
        fiveYears => _monthsBefore(lastDay, 60),
      };
}

DateTime _monthsBefore(DateTime day, int months) {
  final index = day.year * 12 + (day.month - 1) - months;
  final year = index ~/ 12;
  final month = index % 12 + 1;
  final length = DateTime.utc(year, month + 1, 0).day;
  return DateTime.utc(year, month, day.day < length ? day.day : length);
}

/// How the total moved across a range: its first point to its last.
///
/// Two stored totals and their difference. Nothing is interpolated: the ends
/// are the first and last readings that exist inside the range, and the screen
/// names the first one's date, so a range longer than the record says how far
/// back it really reaches.
@immutable
class RangeChange {
  const RangeChange._(this.from, this.to);

  /// Null when the range holds fewer than two points: one reading has no change.
  static RangeChange? over(NetWorthHistory range) => range.points.length < 2
      ? null
      : RangeChange._(range.points.first, range.points.last);

  final HistoryPoint from;
  final HistoryPoint to;

  /// The difference, in minor units of the series' one currency.
  Money get change => Money(
        minorUnits: to.total.amount.minorUnits - from.total.amount.minorUnits,
        currency: to.total.amount.currency,
      );

  /// The change as hundredths of a percent of the starting total, rounded half
  /// away from zero — integers throughout (`AGENTS.md`: money is never a float).
  ///
  /// Null when the starting total is zero or negative: a percentage of nothing,
  /// or of a debt, is a number with no meaning, so none is shown.
  int? get percentHundredths {
    final base = from.total.amount.minorUnits;
    if (base <= 0) {
      return null;
    }
    final scaled = change.minorUnits * 10000;
    var quotient = scaled ~/ base;
    if (2 * scaled.remainder(base).abs() >= base) {
      quotient += scaled.sign;
    }
    return quotient;
  }
}

/// `+$1,234.56`, `-$80.00`, `$0.00`: [Money.format] with the sign a change needs.
String formatSignedMoney(Money amount) =>
    amount.minorUnits > 0 ? '+${amount.format()}' : amount.format();

/// `+1.23%`, `-0.50%`, `0.00%`, from hundredths of a percent.
String formatSignedPercent(int hundredths) {
  final sign = hundredths > 0 ? '+' : (hundredths < 0 ? '-' : '');
  final magnitude = hundredths.abs();
  final fraction = (magnitude % 100).toString().padLeft(2, '0');
  return '$sign${magnitude ~/ 100}.$fraction%';
}

/// The granularity a time-axis label is written at.
enum AxisUnit { day, month, year }

/// One dated mark under the curve.
@immutable
class AxisTick {
  const AxisTick(this.day, this.unit);

  final DateTime day;
  final AxisUnit unit;

  @override
  bool operator ==(Object other) => other is AxisTick && other.day == day && other.unit == unit;

  @override
  int get hashCode => Object.hash(day, unit);

  @override
  String toString() => 'AxisTick(${day.toIso8601String().substring(0, 10)}, ${unit.name})';
}

/// Where the time axis is labelled, for a range drawn from [first] to [last].
///
/// The finest calendar step that gives between two and [maxTicks] marks inside
/// the range: days for a few weeks, month starts for a few months, new years
/// beyond. A range of a single day is labelled once, under its one point.
List<AxisTick> axisTicks(DateTime first, DateTime last, {int maxTicks = 5}) {
  if (!last.isAfter(first)) {
    return [AxisTick(first, AxisUnit.day)];
  }
  for (final (unit, step) in _steps) {
    final ticks = _ticks(first, last, unit, step);
    if (ticks.length >= 2 && ticks.length <= maxTicks) {
      return ticks;
    }
  }
  return [AxisTick(first, AxisUnit.day), AxisTick(last, AxisUnit.day)];
}

const _steps = [
  (AxisUnit.day, 1),
  (AxisUnit.day, 2),
  (AxisUnit.day, 7),
  (AxisUnit.day, 14),
  (AxisUnit.month, 1),
  (AxisUnit.month, 2),
  (AxisUnit.month, 3),
  (AxisUnit.month, 6),
  (AxisUnit.year, 1),
  (AxisUnit.year, 2),
  (AxisUnit.year, 5),
];

List<AxisTick> _ticks(DateTime first, DateTime last, AxisUnit unit, int step) {
  final ticks = <AxisTick>[];
  switch (unit) {
    case AxisUnit.day:
      for (var day = first; !day.isAfter(last); day = day.add(Duration(days: step))) {
        ticks.add(AxisTick(day, unit));
      }
    case AxisUnit.month:
      // Month starts whose month is a multiple of the step: quarters fall on
      // January, April, July and October rather than wherever the range began.
      for (var index = first.year * 12 + first.month - 1;; index++) {
        final day = DateTime.utc(index ~/ 12, index % 12 + 1);
        if (day.isAfter(last)) {
          break;
        }
        if (!day.isBefore(first) && (index % 12) % step == 0) {
          // A January mark is the year turning, and is labelled as one.
          ticks.add(AxisTick(day, index % 12 == 0 ? AxisUnit.year : AxisUnit.month));
        }
      }
    case AxisUnit.year:
      for (var year = first.year;; year++) {
        final day = DateTime.utc(year);
        if (day.isAfter(last)) {
          break;
        }
        if (!day.isBefore(first) && year % step == 0) {
          ticks.add(AxisTick(day, unit));
        }
      }
  }
  return ticks;
}
