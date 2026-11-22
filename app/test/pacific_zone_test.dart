import 'package:flutter_test/flutter_test.dart';
import 'pacific_zone.dart';

/// Every daylight-saving change in Los Angeles from 2007 to 2040, as the IANA
/// database has them: the UTC hour PDT begins, and the UTC hour it ends.
///
/// Derived from Python's `zoneinfo` on 2026-11-21 rather than from the rule in
/// `pacific_zone.dart`, so a mistake in the fixture cannot also be in its test.
const _transitions = [
  (2007, '03-11T10', '11-04T09'),
  (2008, '03-09T10', '11-02T09'),
  (2009, '03-08T10', '11-01T09'),
  (2010, '03-14T10', '11-07T09'),
  (2011, '03-13T10', '11-06T09'),
  (2012, '03-11T10', '11-04T09'),
  (2013, '03-10T10', '11-03T09'),
  (2014, '03-09T10', '11-02T09'),
  (2015, '03-08T10', '11-01T09'),
  (2016, '03-13T10', '11-06T09'),
  (2017, '03-12T10', '11-05T09'),
  (2018, '03-11T10', '11-04T09'),
  (2019, '03-10T10', '11-03T09'),
  (2020, '03-08T10', '11-01T09'),
  (2021, '03-14T10', '11-07T09'),
  (2022, '03-13T10', '11-06T09'),
  (2023, '03-12T10', '11-05T09'),
  (2024, '03-10T10', '11-03T09'),
  (2025, '03-09T10', '11-02T09'),
  (2026, '03-08T10', '11-01T09'),
  (2027, '03-14T10', '11-07T09'),
  (2028, '03-12T10', '11-05T09'),
  (2029, '03-11T10', '11-04T09'),
  (2030, '03-10T10', '11-03T09'),
  (2031, '03-09T10', '11-02T09'),
  (2032, '03-14T10', '11-07T09'),
  (2033, '03-13T10', '11-06T09'),
  (2034, '03-12T10', '11-05T09'),
  (2035, '03-11T10', '11-04T09'),
  (2036, '03-09T10', '11-02T09'),
  (2037, '03-08T10', '11-01T09'),
  (2038, '03-14T10', '11-07T09'),
  (2039, '03-13T10', '11-06T09'),
  (2040, '03-11T10', '11-04T09'),
];

DateTime _at(int year, String monthDayHour) =>
    DateTime.parse('$year-$monthDayHour:00:00Z');

void main() {
  test('daylight time starts and ends on the IANA database\'s second, every year', () {
    const second = Duration(seconds: 1);
    for (final (year, starts, ends) in _transitions) {
      final start = _at(year, starts);
      final end = _at(year, ends);
      expect(PacificZone.isDaylight(start.subtract(second)), isFalse, reason: '$year start');
      expect(PacificZone.isDaylight(start), isTrue, reason: '$year start');
      expect(PacificZone.isDaylight(end.subtract(second)), isTrue, reason: '$year end');
      expect(PacificZone.isDaylight(end), isFalse, reason: '$year end');
    }
  });

  test('the wall clock is the UTC instant moved by the offset in force', () {
    // The owner's first real snapshot: 00:19Z on the 22nd is his afternoon of
    // the 21st, and the day it belongs on is the 21st.
    final winter = const PacificZone().of(DateTime.parse('2026-11-22T00:19:18Z'));
    expect(winter.zoneName, 'PST');
    expect(winter.wallClock, DateTime.utc(2026, 11, 21, 16, 19, 18));
    expect(winter.day, DateTime.utc(2026, 11, 21));

    final summer = const PacificZone().of(DateTime.parse('2026-09-15T04:00:00Z'));
    expect(summer.zoneName, 'PDT');
    expect(summer.wallClock, DateTime.utc(2026, 9, 14, 21));
    expect(summer.day, DateTime.utc(2026, 9, 14));
  });

  test('the day changes at local midnight on both nights the offset changes', () {
    // 07:30Z is the hour where PST and PDT disagree about the date.
    expect(const PacificZone().of(DateTime.parse('2026-03-09T06:30:00Z')).day, DateTime.utc(2026, 3, 8));
    expect(const PacificZone().of(DateTime.parse('2026-03-09T07:30:00Z')).day, DateTime.utc(2026, 3, 9));
    expect(const PacificZone().of(DateTime.parse('2026-11-02T07:30:00Z')).day, DateTime.utc(2026, 11, 1));
    expect(const PacificZone().of(DateTime.parse('2026-11-02T08:30:00Z')).day, DateTime.utc(2026, 11, 2));
  });

  test('the device zone has no say', () {
    // CI runs in Asia/Kolkata and this machine in Los Angeles; a local DateTime
    // is converted first, so every hour of one day lands the same way on both.
    for (var hour = 0; hour < 24; hour++) {
      final utc = DateTime.utc(2026, 9, 10, hour, 30);
      expect(const PacificZone().of(utc.toLocal()).wallClock, const PacificZone().of(utc).wallClock);
      expect(const PacificZone().of(utc.toLocal()).day, const PacificZone().of(utc).day);
    }
  });
}
