import 'package:flutter/foundation.dart';

/// An instant as it reads in the owner's zone, `America/Los_Angeles`.
///
/// **Computed from the rules, not looked up.** `AGENTS.md` cites a sibling
/// project that shipped a "no notification" bug which came down to a timezone
/// lookup failing on the device, and no unit test saw it. There is no lookup
/// here to fail: Pacific Standard Time is UTC−8, and Pacific Daylight Time
/// (UTC−7) runs from 02:00 local on the second Sunday of March to 02:00 local
/// on the first Sunday of November — the US rule since 2007. Every instant this
/// app holds is from 2026 or later. If the law changes, this file changes.
///
/// Storage, staleness and the wire stay UTC (`AGENTS.md`); this is only how a
/// time is *shown*, and which calendar day a reading is drawn on.
@immutable
class PacificTime {
  const PacificTime._(this.instant, this.isDaylight);

  factory PacificTime.of(DateTime instant) {
    final utc = instant.toUtc();
    return PacificTime._(utc, _isDaylight(utc));
  }

  /// The instant itself, in UTC.
  final DateTime instant;

  /// PDT rather than PST.
  final bool isDaylight;

  Duration get offset => Duration(hours: isDaylight ? -7 : -8);

  /// The wall clock in Los Angeles, carried in a UTC-flagged [DateTime] whose
  /// fields are the local ones — the form a formatter can print. **Not an
  /// instant:** comparing it with one is off by the offset.
  DateTime get wallClock => instant.add(offset);

  /// The Los Angeles calendar day, as UTC midnight of that date.
  ///
  /// UTC midnight is only the container: two such days are a whole number of
  /// 24-hour days apart, so day arithmetic on them is exact.
  DateTime get day {
    final local = wallClock;
    return DateTime.utc(local.year, local.month, local.day);
  }
}

/// The Los Angeles calendar day [instant] falls on (see [PacificTime.day]).
DateTime pacificDayOf(DateTime instant) => PacificTime.of(instant).day;

bool _isDaylight(DateTime utc) {
  // DST never touches December or January, so the UTC year is the local year
  // whenever the answer could depend on it.
  final year = utc.year;
  // 02:00 PST is 10:00Z; 02:00 PDT is 09:00Z.
  final starts = DateTime.utc(year, DateTime.march, _sunday(year, DateTime.march, 2), 10);
  final ends = DateTime.utc(year, DateTime.november, _sunday(year, DateTime.november, 1), 9);
  return !utc.isBefore(starts) && utc.isBefore(ends);
}

/// The date of the [nth] Sunday of [month].
int _sunday(int year, int month, int nth) {
  final first = DateTime.utc(year, month).weekday;
  return 1 + (DateTime.sunday - first) % 7 + 7 * (nth - 1);
}
