import 'package:flutter/material.dart';
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
  await tester.pumpWidget(
    MaterialApp(home: Scaffold(body: Headline(total: total))),
  );
}

void main() {
  testWidgets('KNOWN shows the amount and its date', (tester) async {
    await _pump(tester, loadFixture(knownFixture).total);

    expect(find.text(r'$42,500.00'), findsOneWidget);
    expect(find.text('as of 14 Sep 2026, 20:15 UTC'), findsOneWidget);
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
    expect(
      find.text('no linked accounts yet — every value here is a fixed manual entry'),
      findsOneWidget,
    );
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
        amount: base.amount,
        assets: base.assets,
        liabilities: base.liabilities,
        staticAccountCount: 2,
        asOf: base.asOf,
      ),
    );

    expect(find.text('includes 2 fixed manual valuations'), findsOneWidget);
  });

  testWidgets('and are not announced twice when they are all there is', (tester) async {
    await _pump(tester, loadFixture(staticOnlyFixture).total);

    expect(find.textContaining('includes'), findsNothing);
  });
}
