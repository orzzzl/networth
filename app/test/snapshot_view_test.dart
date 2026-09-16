import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/copy_freshness.dart';
import 'package:networth_app/src/domain/phone_payload.dart';
import 'package:networth_app/src/ui/headline.dart';
import 'package:networth_app/src/ui/snapshot_view.dart';

import 'fixtures.dart';

Future<void> _pump(WidgetTester tester, PhonePayload payload, DateTime deviceNow) async {
  await tester.pumpWidget(
    localized(SnapshotView(payload: payload, deviceNow: deviceNow)),
  );
}

void main() {
  testWidgets(
    'a fresh institution clock and a stale copy are both reported, separately',
    (tester) async {
      // This is invariant I4 with the collapse made concrete. The payload's own
      // data is perfectly dated — `age_state: KNOWN`, yesterday evening — while
      // the phone is holding a copy days past its deadline. An app with one
      // combined indicator shows "as of 14 Sep" and looks healthy, which is the
      // original lie reproduced inside the product built to refuse it.
      await _pump(tester, loadFixture(knownFixture), DateTime.utc(2026, 9, 20));

      expect(find.text('as of Sep 14, 2026, 20:15 UTC'), findsOneWidget);
      expect(find.text('all reporting normally'), findsOneWidget);
      expect(
        find.text('overdue — nothing new since Sep 15, 2026, 04:00 UTC'),
        findsOneWidget,
      );
    },
  );

  testWidgets('the two dimensions are two rows, never one badge', (tester) async {
    await _pump(tester, loadFixture(knownFixture), DateTime.utc(2026, 9, 20));

    expect(find.text('Accounts'), findsOneWidget);
    expect(find.text('This copy'), findsOneWidget);
  });

  testWidgets('a fresh copy says when it was published', (tester) async {
    await _pump(tester, loadFixture(knownFixture), DateTime.utc(2026, 9, 15, 12));

    expect(find.text('published Sep 15, 2026, 04:00 UTC'), findsOneWidget);
  });

  testWidgets('a disagreeing device clock is named as such', (tester) async {
    await _pump(tester, loadFixture(knownFixture), DateTime.utc(2026, 9, 15, 3));

    expect(find.text("this device's clock disagrees with the server's"), findsOneWidget);
  });

  testWidgets('WAITING never reads as a demand to re-link', (tester) async {
    // Section 9.2 split the connection axis precisely so the "no owner action"
    // states could not be rendered as a re-link prompt.
    await _pump(tester, loadFixture(mixedFixture), DateTime.utc(2026, 9, 15, 12));

    expect(find.text('some data is behind — nothing for you to do'), findsOneWidget);
    expect(find.textContaining('reconnect'), findsNothing);
  });

  testWidgets('ACTION_NEEDED names no remedy, because the wire names no cause', (
    tester,
  ) async {
    // PR #77 review blocker 2. `networth/staleness.py` returns ACTION_NEEDED for
    // three different causes — an unreconciled account, an institution needing
    // re-authentication, and a frozen source clock — whose next actions are
    // reconciliation, reconnection and neither. The old copy said "an account
    // needs to be reconnected" for all three, so it sent the owner to the wrong
    // place two thirds of the time. The payload carries one enum and no cause.
    //
    // Constructed rather than read from a fixture: no shipped fixture carries
    // ACTION_NEEDED, and inventing one to satisfy a test would be adding a
    // fixture the daemon does not send.
    final base = loadFixture(knownFixture);
    await _pump(
      tester,
      PhonePayload(
        schemaVersion: base.schemaVersion,
        pairingId: base.pairingId,
        seq: base.seq,
        publishedAt: base.publishedAt,
        publishInterval: base.publishInterval,
        grace: base.grace,
        total: base.total,
        connectionState: ConnectionDisplayState.actionNeeded,
      ),
      DateTime.utc(2026, 9, 15, 12),
    );

    expect(find.text('an account needs your attention'), findsOneWidget);
    for (final word in ['reconnect', 'reconcile', 'frozen', 'link']) {
      expect(
        find.textContaining(word, findRichText: true),
        findsNothing,
        reason: 'ACTION_NEEDED named a remedy the payload does not identify: $word',
      );
    }
  });

  testWidgets('the headline is present in every fixture at every clock', (tester) async {
    for (final fixture in allFixtures) {
      for (final now in [DateTime.utc(2026, 9, 15, 12), DateTime.utc(2026, 9, 20)]) {
        await _pump(tester, loadFixture(fixture), now);
        expect(find.byType(Headline), findsOneWidget, reason: '$fixture at $now');
      }
    }
  });
}
