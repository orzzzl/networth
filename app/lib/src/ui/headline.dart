import 'package:flutter/material.dart';

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
    final staticNote = _staticNote(total);
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
          _ageAnnotation(total),
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
/// The `UNKNOWN` wording is quoted from §8.1 R3 rather than paraphrased, because
/// the section argues at length that a count beside a confident date does not
/// stop the date being read — so the date has to be absent, and the sentence has
/// to say what is missing and how much of the money it covers.
String _ageAnnotation(DatedTotal total) => switch (total) {
      KnownAgeTotal(:final asOf) => 'as of ${formatInstantUtc(asOf)}',
      UndatableTotal(:final undatableAccountCount, :final accountCount) =>
        "can't date this total — $undatableAccountCount of $accountCount "
            "${accountCount == 1 ? 'account' : 'accounts'} can't be dated",
      StaticOnlyTotal() => 'no linked accounts yet — every value here is a fixed manual entry',
    };

/// §8.1: fixed valuations sit outside the age basis, and saying so out loud is
/// the condition on which leaving them out is honest rather than hiding them.
/// `StaticOnlyTotal` already says it in the annotation above, so it is not
/// repeated here.
String? _staticNote(DatedTotal total) {
  if (total is StaticOnlyTotal || total.staticAccountCount == 0) {
    return null;
  }
  final count = total.staticAccountCount;
  final noun = count == 1 ? 'valuation' : 'valuations';
  return 'includes $count fixed manual $noun';
}
