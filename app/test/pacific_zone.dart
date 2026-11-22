import 'package:networth_app/src/domain/display_zone.dart';

/// `America/Los_Angeles`, computed — the zone the whole suite reads times in.
///
/// The app shows times in the device's own zone ([DeviceZone]), and a test's
/// device zone is wherever it runs: this machine is in Los Angeles and CI runs
/// in `Asia/Kolkata` on purpose. So `flutter_test_config.dart` pins this zone
/// for every test, and the expected strings mean the same thing everywhere.
/// [DeviceZone] itself is tested against the platform in `device_zone_test.dart`.
///
/// PST is UTC−8; PDT (UTC−7) runs from 02:00 local on the second Sunday of March
/// to 02:00 local on the first Sunday of November — the US rule since 2007,
/// checked against the IANA database in `pacific_zone_test.dart`.
class PacificZone implements DisplayZone {
  const PacificZone();

  @override
  ZonedTime of(DateTime instant) {
    final utc = instant.toUtc();
    final daylight = isDaylight(utc);
    return ZonedTime(
      wallClock: utc.add(Duration(hours: daylight ? -7 : -8)),
      zoneName: daylight ? 'PDT' : 'PST',
    );
  }

  static bool isDaylight(DateTime utc) {
    // DST never touches December or January, so the UTC year is the local year
    // whenever the answer could depend on it. 02:00 PST is 10:00Z; 02:00 PDT is
    // 09:00Z.
    final year = utc.year;
    final starts = DateTime.utc(year, DateTime.march, _sunday(year, DateTime.march, 2), 10);
    final ends = DateTime.utc(year, DateTime.november, _sunday(year, DateTime.november, 1), 9);
    return !utc.isBefore(starts) && utc.isBefore(ends);
  }

  /// The date of the [nth] Sunday of [month].
  static int _sunday(int year, int month, int nth) {
    final first = DateTime.utc(year, month).weekday;
    return 1 + (DateTime.sunday - first) % 7 + 7 * (nth - 1);
  }
}
