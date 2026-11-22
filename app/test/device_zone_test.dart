import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:networth_app/l10n/generated/app_localizations_en.dart';
import 'package:networth_app/src/domain/display_zone.dart';
import 'package:networth_app/src/ui/instant.dart';

import 'pacific_zone.dart';

/// The zone the app actually ships with, against the platform it runs on.
///
/// Every other test reads times in a pinned zone (`flutter_test_config.dart`);
/// this file is where the device's own zone is checked, so it is the one that
/// means something different on each runner — Los Angeles here, Asia/Kolkata on
/// CI — and it asserts the relation that must hold on both.
void main() {
  setUpAll(() => initializeDateFormatting('en'));

  test('a wall clock is the platform\'s local time, and the zone is the platform\'s name', () {
    const zone = DeviceZone();
    // Every 7 hours across 2026: both DST changes, every hour of the day.
    for (var hours = 0; hours < 24 * 365; hours += 7) {
      final instant = DateTime.utc(2026).add(Duration(hours: hours));
      final local = instant.toLocal();
      final zoned = zone.of(instant);
      expect(zoned.wallClock.difference(instant), local.timeZoneOffset, reason: '$instant');
      expect(zoned.wallClock.hour, local.hour, reason: '$instant');
      expect(zoned.day, DateTime.utc(local.year, local.month, local.day), reason: '$instant');
      expect(zoned.zoneName, local.timeZoneName, reason: '$instant');
    }
  });

  test('a device-local DateTime is the same instant as its UTC self', () {
    final instant = DateTime.utc(2026, 9, 15, 4);
    expect(const DeviceZone().of(instant.toLocal()).wallClock, const DeviceZone().of(instant).wallClock);
  });

  test('the text names the device zone', () {
    displayZone = const DeviceZone();
    addTearDown(() => displayZone = const PacificZone());
    final instant = DateTime.utc(2026, 7, 15, 19);

    expect(
      formatInstant(AppLocalizationsEn(), instant),
      endsWith(' ${instant.toLocal().timeZoneName}'),
    );
  });

  test('off UTC, it actually converts', () {
    // The relation above holds trivially for an implementation that never
    // converts, on a runner in UTC. CI is not in UTC, so there this can fail.
    final instant = DateTime.utc(2026, 7, 15, 19);
    expect(const DeviceZone().of(instant).wallClock, isNot(instant));
  }, skip: DateTime.utc(2026, 7, 15, 19).toLocal().timeZoneOffset == Duration.zero
      ? 'runner is on UTC: this cannot distinguish converting from not'
      : false);
}
