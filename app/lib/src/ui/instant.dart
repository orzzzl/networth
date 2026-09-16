import '../../l10n/generated/app_localizations.dart';

/// Render an instant in UTC, with the zone named.
///
/// `AGENTS.md` requires every stored timestamp to be UTC with an explicit zone,
/// and a sibling project has already shipped a "no notification" bug that came
/// down to a timezone lookup. Showing the zone rather than quietly converting to
/// whatever the device believes local time is keeps the displayed date and the
/// stored one the same fact.
///
/// The month name is the locale's, not this file's. An earlier version kept a
/// `const List<String> _months` here and called localised formatting "the rest
/// of the i18n work" — but a hard-coded English month name in a date the owner
/// reads is exactly the thing the convention forbids, and leaving it for later
/// meant every screen built on top of this one would inherit it. The ARB entry
/// declares the date and the time as `DateTime` placeholders, so `intl` supplies
/// the symbols and the ordering for whatever locale is resolved.
///
/// The [DateTime] is converted with [DateTime.toUtc] first, so the fields `intl`
/// formats are the UTC ones regardless of what the caller passed in.
String formatInstantUtc(AppLocalizations l10n, DateTime instant) {
  final utc = instant.toUtc();
  return l10n.instantUtc(utc, utc);
}
