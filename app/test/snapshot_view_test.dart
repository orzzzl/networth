import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/copy_freshness.dart';
import 'package:networth_app/src/domain/net_worth_history.dart';
import 'package:networth_app/src/domain/phone_payload.dart';
import 'package:networth_app/src/ui/headline.dart';
import 'package:networth_app/src/ui/snapshot_view.dart';

import 'fixtures.dart';

Future<void> _pump(
  WidgetTester tester,
  PhonePayload payload,
  DateTime deviceNow, {
  NetWorthHistory? history = NetWorthHistory.empty,
  bool recordingFailed = false,
}) async {
  await tester.pumpWidget(
    localized(
      SnapshotView(
        payload: payload,
        history: history,
        recordingFailed: recordingFailed,
        deviceNow: deviceNow,
      ),
    ),
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
    // Still constructed rather than read from a fixture, and the reason changed
    // with this commit. It used to be that no shipped fixture carried
    // ACTION_NEEDED; `alerts_open.json` now does. But this test asserts the
    // *absence* of four words from the entire screen, and that fixture's alert
    // rows name causes and remedies deliberately — so what it needs is
    // ACTION_NEEDED with an **empty** alert set: the connection row on its own,
    // which is the copy under test. The claim being pinned is unchanged and is
    // specifically about that row: a remedy may only be named by something that
    // knows the cause, and one enum does not.
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
        alerts: const [],
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

  group('the screen fits, or scrolls — it never renders overflow stripes', () {
    // Phone landscape, and the same portrait phone with the system text size
    // raised. Both are one rotation or one settings toggle away, and the second
    // is a setting that people who care about reading numbers actually use.
    const cases = <String, (Size, double)>{
      'landscape 640x360': (Size(640, 360), 1.0),
      'portrait 360x640 at 2.0x text': (Size(360, 640), 2.0),
      'landscape 640x360 at 1.5x text': (Size(640, 360), 1.5),
    };

    // Both the fixture this regression was found on and the tallest screen this
    // app can draw. The alert block is new vertical content whose height depends
    // on how many kinds are open, and it lands above the curve — so the case that
    // used to be the worst one no longer is.
    for (final fixture in [mixedFixture, alertsOpenFixture]) {
      for (final entry in cases.entries) {
        testWidgets('${entry.key} — $fixture', (tester) async {
        final (size, textScale) = entry.value;
        tester.view.physicalSize = size;
        tester.view.devicePixelRatio = 1.0;
        addTearDown(tester.view.reset);

        // The shipped series, not an empty one: the curve and both of its notes
        // are exactly the height this regression is about.
        await tester.pumpWidget(
          localized(
            MediaQuery(
              data: MediaQueryData(textScaler: TextScaler.linear(textScale)),
              child: SnapshotView(
                payload: loadFixture(fixture),
                history: demoSeries(),
                recordingFailed: false,
                deviceNow: DateTime.utc(2026, 9, 20),
              ),
            ),
          ),
        );

        // `takeException` rather than a `find` on the yellow stripes: a
        // `RenderFlex overflowed` is reported as a `FlutterError` and the
        // stripes are painted, not built, so nothing in the widget tree would
        // show it.
        expect(
          tester.takeException(),
          isNull,
          reason: '${entry.key} overflowed',
        );
        // And the content is still reachable rather than merely not complained
        // about — a `ClipRect` would also have silenced the exception.
        expect(find.byType(Headline), findsOneWidget);
        await tester.drag(find.byType(SnapshotView), const Offset(0, -400));
        await tester.pump();
        expect(tester.takeException(), isNull);
        });
      }
    }
  });
}
