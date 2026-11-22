import '../../l10n/generated/app_localizations.dart';
import '../domain/pacific_time.dart';

/// Render an instant as the owner reads it: Los Angeles time, zone named.
///
/// He asked for Pacific time by name, so it is Pacific whatever the device is
/// set to — not `toLocal()`, which would make the same stored instant read
/// differently on a phone that travels. The zone is printed (PST or PDT), so a
/// displayed time is never ambiguous about which clock it is on.
///
/// The conversion is [PacificTime]'s, which computes the offset from the rules
/// rather than looking anything up. Storage and the wire stay UTC (`AGENTS.md`);
/// this function is the only place an instant becomes Pacific text.
///
/// The month name is the locale's, not this file's: the ARB entry declares the
/// date and the time as `DateTime` placeholders, so `intl` supplies the symbols
/// and the ordering. What it is handed is [PacificTime.wallClock], whose fields
/// are the Los Angeles ones, and `intl` formats a [DateTime]'s fields as they
/// stand.
String formatInstant(AppLocalizations l10n, DateTime instant) {
  final pacific = PacificTime.of(instant);
  final local = pacific.wallClock;
  return l10n.instantPacific(
    local,
    local,
    pacific.isDaylight ? l10n.zonePacificDaylight : l10n.zonePacificStandard,
  );
}
