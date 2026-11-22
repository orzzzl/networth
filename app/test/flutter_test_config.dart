import 'dart:async';

import 'package:networth_app/src/domain/display_zone.dart';

import 'pacific_zone.dart';

/// Every test reads times in one fixed zone, so an expected string such as
/// `Sep 14, 2026, 21:00 PDT` means the same on this machine and on CI's
/// `Asia/Kolkata`. The app itself shows the device's zone ([DeviceZone]),
/// which `device_zone_test.dart` checks against the platform directly.
Future<void> testExecutable(FutureOr<void> Function() testMain) async {
  displayZone = const PacificZone();
  await testMain();
}
