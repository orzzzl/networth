import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:networth_app/l10n/generated/app_localizations_en.dart';
import 'package:networth_app/src/ui/instant.dart';

void main() {
  setUpAll(() => initializeDateFormatting('en'));

  final l10n = AppLocalizationsEn();

  test('an instant reads as Los Angeles time with its zone named', () {
    // The owner's first real snapshot, which the 0.2.0 screen printed as
    // "Nov 22, 2026, 00:19 UTC" — a date that had not arrived yet where he was.
    expect(formatInstant(l10n, DateTime.parse('2026-11-22T00:19:18Z')), 'Nov 21, 2026, 16:19 PST');
  });

  test('either side of the November change', () {
    expect(formatInstant(l10n, DateTime.parse('2026-11-01T08:59:00Z')), 'Nov 1, 2026, 01:59 PDT');
    expect(formatInstant(l10n, DateTime.parse('2026-11-01T09:00:00Z')), 'Nov 1, 2026, 01:00 PST');
  });

  test('either side of the March change', () {
    expect(formatInstant(l10n, DateTime.parse('2026-03-08T09:59:00Z')), 'Mar 8, 2026, 01:59 PST');
    expect(formatInstant(l10n, DateTime.parse('2026-03-08T10:00:00Z')), 'Mar 8, 2026, 03:00 PDT');
  });

  test('a device-local instant reads the same as its UTC self', () {
    final utc = DateTime.parse('2026-09-15T04:00:00Z');
    expect(formatInstant(l10n, utc.toLocal()), formatInstant(l10n, utc));
    expect(formatInstant(l10n, utc), 'Sep 14, 2026, 21:00 PDT');
  });
}
