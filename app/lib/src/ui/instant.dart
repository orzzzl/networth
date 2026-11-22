import '../../l10n/generated/app_localizations.dart';
import '../domain/display_zone.dart';

/// Render an instant as the owner reads it: the phone's own time, zone named.
///
/// The owner asked for his phone's local time (2026-11-21), so this is
/// [displayZone] — the device's zone — and the zone is printed as the platform
/// names it (`PST`, `PDT`; an offset such as `+04` where a zone has no
/// abbreviation), so a displayed time is never ambiguous about which clock it
/// is on. Storage and the wire stay UTC (`AGENTS.md`); this function is the
/// only place an instant becomes text.
///
/// The month name is the locale's, not this file's: the ARB entry declares the
/// date and the time as `DateTime` placeholders, so `intl` supplies the symbols
/// and the ordering. What it is handed is [ZonedTime.wallClock], whose fields
/// are the zone's own, and `intl` formats a [DateTime]'s fields as they stand.
String formatInstant(AppLocalizations l10n, DateTime instant) {
  final zoned = displayZone.of(instant);
  return l10n.instantZoned(zoned.wallClock, zoned.wallClock, zoned.zoneName);
}
