const List<String> _months = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/// Render an instant in UTC, with the zone named.
///
/// `AGENTS.md` requires every stored timestamp to be UTC with an explicit zone,
/// and a sibling project has already shipped a "no notification" bug that came
/// down to a timezone lookup. Showing the zone rather than quietly converting to
/// whatever the device believes local time is keeps the displayed date and the
/// stored one the same fact. Localised formatting belongs with the rest of the
/// i18n work, not in a skeleton that would have to guess at a locale.
String formatInstantUtc(DateTime instant) {
  final utc = instant.toUtc();
  final day = utc.day.toString().padLeft(2, '0');
  final month = _months[utc.month - 1];
  final hour = utc.hour.toString().padLeft(2, '0');
  final minute = utc.minute.toString().padLeft(2, '0');
  return '$day $month ${utc.year}, $hour:$minute UTC';
}
