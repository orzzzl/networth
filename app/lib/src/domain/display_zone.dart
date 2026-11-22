import 'package:flutter/foundation.dart';

/// An instant as the owner reads it: the wall clock, and the name of its zone.
@immutable
class ZonedTime {
  const ZonedTime({required this.wallClock, required this.zoneName});

  /// The wall clock, carried in a UTC-flagged [DateTime] whose fields are the
  /// zone's own — the form a formatter prints. **Not an instant:** comparing it
  /// with one is off by the zone's offset.
  final DateTime wallClock;

  /// The zone's name as the platform gives it: `PST`, `PDT`, `IST`, or an
  /// offset such as `+04` for a zone with no abbreviation.
  final String zoneName;

  /// The calendar day, as UTC midnight of that date.
  ///
  /// UTC midnight is only the container: two such days are a whole number of
  /// 24-hour days apart, so day arithmetic on them is exact.
  DateTime get day => DateTime.utc(wallClock.year, wallClock.month, wallClock.day);
}

/// Where an instant is shown.
abstract interface class DisplayZone {
  ZonedTime of(DateTime instant);
}

/// The phone's own time zone — the owner's choice (2026-11-21: *"最好的话是用手机本机的时间"*).
///
/// He is in Pacific time, so he reads PST or PDT; when the phone travels, the
/// times follow it. The offset and the name come from the platform's own zone
/// rules through [DateTime.toLocal], so daylight saving is the operating
/// system's to get right, not this app's.
///
/// Storage, staleness and the wire stay UTC (`AGENTS.md`); this is only how a
/// time is *shown*, and which calendar day a reading is drawn on.
class DeviceZone implements DisplayZone {
  const DeviceZone();

  @override
  ZonedTime of(DateTime instant) {
    final local = instant.toLocal();
    return ZonedTime(
      wallClock: DateTime.utc(
        local.year,
        local.month,
        local.day,
        local.hour,
        local.minute,
        local.second,
        local.millisecond,
        local.microsecond,
      ),
      zoneName: local.timeZoneName,
    );
  }
}

/// The zone every time on screen is shown in: the device's.
///
/// Assignable for one reason: `test/flutter_test_config.dart` pins a fixed zone
/// so the suite's expected strings do not depend on where it runs (CI runs in
/// `Asia/Kolkata` on purpose). Nothing in `lib/` assigns it.
DisplayZone displayZone = const DeviceZone();

/// The calendar day [instant] falls on where the phone is (see [ZonedTime.day]).
DateTime localDayOf(DateTime instant) => displayZone.of(instant).day;
