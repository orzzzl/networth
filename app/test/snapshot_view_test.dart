import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/clock_continuity.dart';
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
  ClockContinuity continuity = trustedClock,
}) async {
  await tester.pumpWidget(
    localized(
      SnapshotView(
        payload: payload,
        history: history,
        recordingFailed: recordingFailed,
        deviceNow: deviceNow,
        continuity: continuity,
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
      expect(find.text('showing a copy from Sep 15, 2026, 04:00 UTC'), findsOneWidget);
    },
  );

  testWidgets(
    'a stale copy this phone never checked blames nobody for it',
    (tester) async {
      // **The assertion this test replaced was the defect.** It expected
      // `overdue — nothing new since Sep 15, 2026, 04:00 UTC` on exactly this
      // input, and that sentence is §9.1's `HOST_NOT_PUBLISHING` — *"reached the
      // source; nothing has been published since"*. This build performs no
      // fetches, so its diagnostics are [DiagnosticsAbsent] and the predicate
      // reaches `CANNOT_CHECK(NeverFetched)`: the phone has never spoken to the
      // host at all. The old line accused it anyway, on the strength of the
      // stale *indicator*, which is the misattribution §9.1 exists to prevent —
      // and it was the copy on the default screen of the shipped build.
      await _pump(tester, loadFixture(knownFixture), DateTime.utc(2026, 9, 20));

      expect(find.text("this device hasn't checked yet"), findsOneWidget);
      // Nothing on this screen may claim the host published nothing, and the
      // check is on the substring rather than the whole sentence so a reworded
      // version of the same accusation cannot slip past it.
      expect(find.textContaining('nothing newer has been published'), findsNothing);
      expect(find.textContaining('nothing new since'), findsNothing);
    },
  );

  testWidgets('the claim and its cause are two lines, not one sentence', (tester) async {
    // §9.2 keeps the dimensions apart; this is the same argument one level in.
    // The age of the copy is a fact about this phone and the cause is a fact
    // about why it cannot say more, and a reader must be able to believe the
    // first without the second. Concatenating them also means they wrap as one
    // block at large system text sizes and a screen reader announces them as one
    // utterance.
    await _pump(tester, loadFixture(knownFixture), DateTime.utc(2026, 9, 20));

    expect(find.text('showing a copy from Sep 15, 2026, 04:00 UTC'), findsOneWidget);
    expect(find.text("this device hasn't checked yet"), findsOneWidget);
    expect(
      find.textContaining("Sep 15, 2026, 04:00 UTC — this device hasn't checked"),
      findsNothing,
    );
  });

  testWidgets('a fresh copy gets no second line at all', (tester) async {
    // A row that always carries a cause teaches the eye to skip the cause.
    await _pump(tester, loadFixture(knownFixture), DateTime.utc(2026, 9, 15, 12));

    expect(find.text('published Sep 15, 2026, 04:00 UTC'), findsOneWidget);
    expect(find.textContaining("hasn't checked"), findsNothing);
    // §9.2 rule 3's qualifier belongs to a copy that is *not* current. Over a
    // fresh one the payload's connection state and now are the same thing, and
    // the label would be noise.
    expect(find.text('as of this copy, not now'), findsNothing);
  });

  testWidgets(
    'over a copy that is not current, the connection state is labelled historical',
    (tester) async {
      // §9.2's third implementation rule: the state shown came *out of the
      // payload*, so on a stale copy it describes then and not now. Presenting
      // it unqualified is the quiet version of the collapse I4 forbids — the
      // screen would be sourcing one row from the copy's age and the other from
      // an implied present.
      await _pump(tester, loadFixture(knownFixture), DateTime.utc(2026, 9, 20));

      expect(find.text('all reporting normally'), findsOneWidget);
      expect(find.text('as of this copy, not now'), findsOneWidget);
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

    expect(find.text('showing a copy dated Sep 15, 2026, 04:00 UTC'), findsOneWidget);
    expect(
      find.text("the copy is dated ahead of this device's clock, so its age can't be worked out"),
      findsOneWidget,
    );
  });

  testWidgets(
    "a clock that cannot be confirmed is not a clock that was caught",
    (tester) async {
      // §9.1 rule 1 keeps these three apart and the old copy merged them: one
      // string, *"this device's clock disagrees with the server's"*, served all
      // of `payloadFromTheFuture`, `deviceClockMovedBackwards` and
      // `clockContinuityUnknown`. Only the first is about the two clocks
      // disagreeing; the second is this device disagreeing with **itself** and is
      // the only one fixable here; the third is the *absence* of evidence, and
      // calling that a disagreement is the confident answer §9.1 forbids.
      //
      // The third is also what the shipped build actually renders today, since
      // `HomePage` fails closed to `ContinuityGap.noAnchor` until this task's
      // platform monotonic source exists — so the merged string was wrong on the
      // default screen, not in a corner.
      await _pump(
        tester,
        loadFixture(knownFixture),
        DateTime.utc(2026, 9, 15, 12),
        continuity: const ContinuityUnknown(ContinuityGap.noAnchor),
      );

      expect(
        find.text("this device can't confirm its own clock, so its age can't be worked out"),
        findsOneWidget,
      );
      expect(find.textContaining('moved backwards'), findsNothing);
      expect(find.textContaining('dated ahead'), findsNothing);
    },
  );

  testWidgets('a clock caught moving backwards says so, and it is the fixable one', (
    tester,
  ) async {
    await _pump(
      tester,
      loadFixture(knownFixture),
      DateTime.utc(2026, 9, 15, 12),
      // Wall time ran backwards against a monotonic counter that did not: two of
      // this device's own readings contradicting each other.
      continuity: const ContinuityHeld(
        wallElapsed: Duration(hours: -3),
        monotonicElapsed: Duration(hours: 1),
      ),
    );

    expect(
      find.text("this device's clock has moved backwards, so its age can't be worked out"),
      findsOneWidget,
    );
    expect(find.textContaining("can't confirm its own clock"), findsNothing);
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
                continuity: trustedClock,
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
