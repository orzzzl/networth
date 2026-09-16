import 'payload_format_exception.dart';

/// Parse a wire timestamp, refusing any that does not carry a timezone.
///
/// `AGENTS.md` requires every stored timestamp to be UTC with an explicit zone,
/// and the parsing this replaced did not enforce it. `DateTime.tryParse` accepts
/// `2026-09-15T04:00:00` happily and treats it as **device-local** time, so
/// `.toUtc()` then invents a different instant on every phone: measured on this
/// machine, that string becomes `2026-09-15T11:00:00Z` at `-07:00` and would
/// become `2026-09-14T20:00:00Z` at `+08:00`. That is not a cosmetic difference.
/// It moves the displayed source date, and it moves the copy-staleness decision
/// that compares `published_at` against the device clock — the second dimension
/// of invariant **I4** — so a zone-less string could make a stale copy look fresh
/// on one phone and overdue on another from the same bytes.
///
/// **The discriminator is measured rather than assumed.** `DateTime.parse` sets
/// `isUtc` when and only when the input carried a zone designator: `Z`, `z` or a
/// `±HH:MM` offset all produce `isUtc == true` (a non-zero offset is converted to
/// UTC), while a bare date, a bare date-time, and a space-separated date-time all
/// produce `isUtc == false`. So `isUtc` answers exactly the question being asked,
/// and `test/instant_test.dart` pins that behaviour rather than trusting it to
/// stay true.
///
/// Refusing is the right shape for the same reason [PayloadFormatException]
/// exists at all: a timestamp we cannot read is not one we may guess at. The
/// total would still render, and it would render a number whose age we invented.
DateTime parseWireInstant(String value, {required String field}) {
  final parsed = DateTime.tryParse(value);
  if (parsed == null) {
    throw PayloadFormatException('$field is not a timestamp');
  }
  if (!parsed.isUtc) {
    throw PayloadFormatException(
      '$field has no timezone; an instant without one is a different instant on '
      'every device',
    );
  }
  return parsed.toUtc();
}
