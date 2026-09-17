import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/dated_total.dart';
import 'package:networth_app/src/ui/headline.dart';

import 'fixtures.dart';

/// Anything that reads as a date or a time: ISO, an English month, or a clock.
final RegExp _dateShaped = RegExp(
  r'\d{4}-\d{2}-\d{2}'
  r'|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b'
  r'|\d{1,2}:\d{2}',
);

Future<void> _pump(WidgetTester tester, DatedTotal total) async {
  await tester.pumpWidget(localized(Headline(total: total)));
}

void main() {
  testWidgets('KNOWN shows the amount and its date', (tester) async {
    await _pump(tester, loadFixture(knownFixture).total);

    expect(find.text(r'$42,500.00'), findsOneWidget);
    // The month name and the field order are the locale's, supplied by `intl`
    // from the ARB's `yMMMd` skeleton — not a `const List<String>` in the app.
    // That is why this reads "Sep 14, 2026" rather than the old hand-built
    // "14 Sep 2026": en_US orders it that way, and a locale added later gets its
    // own ordering without touching Dart.
    expect(find.text('as of Sep 14, 2026, 20:15 UTC'), findsOneWidget);
  });

  testWidgets('UNKNOWN names how much of the total cannot be dated', (tester) async {
    await _pump(tester, loadFixture(mixedFixture).total);

    expect(find.text(r'$42,500.00'), findsOneWidget);
    expect(
      find.text("can't date this total — 1 of 3 accounts can't be dated"),
      findsOneWidget,
    );
  });

  testWidgets(
    'UNKNOWN puts no date anywhere near the headline, though one was available',
    (tester) async {
      // This is the acceptance criterion the mixed fixture exists for. The
      // payload carries `oldest_known_source_as_of`, and the plausible wrong
      // implementation prints it — stating a date that is true of some of the
      // money and unknown for the rest. Asserting on the *whole* rendered
      // surface rather than on one string is deliberate: the failure being
      // guarded against is a date appearing somewhere nobody thought to look.
      await _pump(tester, loadFixture(mixedFixture).total);

      final rendered = renderedText(tester, find.byType(Headline));
      expect(rendered, isNotEmpty);
      for (final line in rendered) {
        expect(
          _dateShaped.hasMatch(line),
          isFalse,
          reason: 'the undatable headline rendered something date-shaped: "$line"',
        );
      }
    },
  );

  testWidgets('STATIC_ONLY says so rather than computing an age over nothing', (tester) async {
    await _pump(tester, loadFixture(staticOnlyFixture).total);

    expect(find.text(r'$680,000.00'), findsOneWidget);
    expect(find.text('no dated source for this total'), findsOneWidget);
  });

  testWidgets('STATIC_ONLY claims an empty age basis and never a cause', (tester) async {
    // PR #77 review blocker 2. The old copy read "no linked accounts yet", which
    // the wire does not prove: `networth/snapshotter.py` skips `NEW` accounts
    // before the clock accounting, so a portfolio whose linked accounts are all
    // still unreconciled reaches `STATIC_ONLY` with linked accounts present.
    // Asserted as an absence over the whole rendered surface, because the defect
    // is a sentence claiming something, not one particular sentence.
    await _pump(tester, loadFixture(staticOnlyFixture).total);

    for (final line in renderedText(tester, find.byType(Headline))) {
      expect(
        line.toLowerCase(),
        isNot(contains('linked')),
        reason: 'STATIC_ONLY asserted something about linked accounts: "$line"',
      );
    }
  });

  testWidgets('every age state renders an annotation beside the amount', (tester) async {
    // The criterion is "no intermediate state renders a bare headline". The
    // structural guarantee is that `DatedTotal` has no ageless constructor; this
    // is the behavioural check that each variant actually puts words on screen.
    for (final fixture in allFixtures) {
      await _pump(tester, loadFixture(fixture).total);

      final rendered = renderedText(tester, find.byType(Headline));
      expect(
        rendered.length,
        greaterThanOrEqualTo(2),
        reason: '$fixture rendered an amount with nothing beside it',
      );
    }
  });

  testWidgets('fixed manual valuations are counted out loud when they are mixed in', (
    tester,
  ) async {
    final base = loadFixture(knownFixture).total as KnownAgeTotal;
    await _pump(
      tester,
      KnownAgeTotal(
        isComplete: true,
        amount: base.amount,
        assets: base.assets,
        liabilities: base.liabilities,
        staticAccountCount: 2,
        asOf: base.asOf,
      ),
    );

    expect(find.text('includes 2 fixed manual valuations'), findsOneWidget);
  });

  testWidgets('and are counted for STATIC_ONLY too, now the annotation is silent', (
    tester,
  ) async {
    // This used to assert the opposite: the note was suppressed for STATIC_ONLY
    // because the annotation said "every value here is a fixed manual entry" in
    // prose. That prose is gone with blocker 2, so the count is now the only
    // place a STATIC_ONLY total says what it is made of — and it is a number off
    // the wire rather than an inference.
    await _pump(tester, loadFixture(staticOnlyFixture).total);

    expect(find.text('includes 1 fixed manual valuation'), findsOneWidget);
  });

  testWidgets('the plural comes from the locale rather than from an inline ?:', (
    tester,
  ) async {
    // One account undatable out of one. The old code chose the noun with
    // `accountCount == 1 ? 'account' : 'accounts'`, which is an English rule
    // compiled into Dart; the ARB's ICU plural is the seam that lets a locale
    // with different rules be a translation rather than a code change. Also the
    // regression for the argument order: gen-l10n orders parameters by the ARB's
    // `placeholders` map, not by their position in the message, and passing them
    // the other way round silently rendered "3 of 1 account".
    final base = loadFixture(mixedFixture).total as UndatableTotal;
    await _pump(
      tester,
      UndatableTotal(
        isComplete: true,
        amount: base.amount,
        assets: base.assets,
        liabilities: base.liabilities,
        staticAccountCount: 0,
        accountCount: 1,
        undatableAccountCount: 1,
      ),
    );

    expect(find.text("can't date this total — 1 of 1 account can't be dated"), findsOneWidget);
  });
}
