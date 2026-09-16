import 'package:flutter/material.dart';

import '../../l10n/generated/app_localizations.dart';
import '../domain/dated_total.dart';
import 'instant.dart';

/// The total and its age, rendered as one widget because they are one fact.
///
/// There is no widget in this app that takes a bare amount. That is deliberate:
/// invariant **I2/I4** says a code path able to emit an unannotated total is a
/// bug, and the cheapest way to honour that is to leave no such path to find.
/// [Headline] accepts a [DatedTotal] — a type with no constructor that omits the
/// age — and the annotation below is built by an exhaustive `switch`, so a new
/// age state added to the sealed type will fail to compile here rather than
/// render as a silent blank.
class Headline extends StatelessWidget {
  const Headline({super.key, required this.total});

  final DatedTotal total;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final l10n = AppLocalizations.of(context);
    final staticNote = _staticNote(l10n, total);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          total.amount.format(),
          style: theme.textTheme.displaySmall?.copyWith(fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 6),
        Text(
          _ageAnnotation(l10n, total),
          style: theme.textTheme.bodyMedium?.copyWith(
            color: total is KnownAgeTotal
                ? theme.colorScheme.onSurfaceVariant
                : theme.colorScheme.error,
          ),
        ),
        if (staticNote != null) ...[
          const SizedBox(height: 4),
          Text(
            staticNote,
            style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ],
      ],
    );
  }
}

/// The one place an age state becomes words.
///
/// Each arm says exactly what its state guarantees and no more. `STATIC_ONLY`
/// used to read "no linked accounts yet", which the wire does not prove: the
/// producer reaches that state whenever no account contributed an advancing
/// clock, and a portfolio whose linked accounts are all still unreconciled does
/// exactly that (`networth/snapshotter.py` skips `NEW` accounts before the clock
/// accounting). So the annotation now reports the empty age basis and leaves the
/// cause to task 22, which parses enough detail to tell the causes apart.
///
/// The `UNKNOWN` wording follows §8.1 R3 rather than paraphrasing it, because
/// the section argues at length that a count beside a confident date does not
/// stop the date being read — so the date has to be absent, and the sentence has
/// to say what is missing and how much of the money it covers. The plural is the
/// ARB's, not an inline `?:`, so a locale whose plural rules are not English's
/// is a translation rather than a code change.
String _ageAnnotation(AppLocalizations l10n, DatedTotal total) => switch (total) {
      KnownAgeTotal(:final asOf) => l10n.totalAsOf(formatInstantUtc(l10n, asOf)),
      // Named locals rather than two bare positionals: gen-l10n orders the
      // generated parameters by the ARB's `placeholders` map, not by where they
      // appear in the message, and the first draft of this call passed them the
      // other way round. Two same-typed positional ints will take each other's
      // place in silence — it rendered "3 of 1 account" — so the call site says
      // which is which.
      UndatableTotal(:final undatableAccountCount, :final accountCount) =>
        l10n.totalUndatable(undatableAccountCount, accountCount),
      StaticOnlyTotal() => l10n.totalNoDatedSource,
    };

/// §8.1: fixed valuations sit outside the age basis, and saying so out loud is
/// the condition on which leaving them out is honest rather than hiding them.
///
/// `StaticOnlyTotal` used to be excluded here because its annotation asserted
/// the same thing in prose. That annotation no longer makes the claim, so this
/// note is where a `STATIC_ONLY` total now says what it is made of — and it says
/// it with a count that came off the wire rather than with an inference.
String? _staticNote(AppLocalizations l10n, DatedTotal total) {
  final count = total.staticAccountCount;
  if (count == 0) {
    return null;
  }
  return l10n.includesStaticValuations(count);
}
