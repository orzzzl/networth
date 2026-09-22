import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/main.dart';
import 'package:networth_app/src/data/history_source.dart';
import 'package:networth_app/src/domain/net_worth_history.dart';
import 'package:networth_app/src/data/snapshot_source.dart';
import 'package:networth_app/src/domain/phone_payload.dart';
import 'package:networth_app/src/ui/headline.dart';
import 'package:networth_app/src/ui/home_page.dart';

import 'fixtures.dart';

/// A source that never completes, so the loading frame can be inspected.
class _PendingSource implements SnapshotSource {
  @override
  bool get isSynthetic => false;

  @override
  Future<PhonePayload> load() => Completer<PhonePayload>().future;
}

class _FailingSource implements SnapshotSource {
  @override
  bool get isSynthetic => false;

  @override
  Future<PhonePayload> load() async => throw StateError('no payload');
}

/// What a phone that has recorded nothing has: the first-launch state of
/// `FileHistoryStore`, without its file.
///
/// A stub rather than the shipped store, for one mechanical reason: the store
/// does real file I/O and `pumpAndSettle` runs on fake time, so a widget test
/// pointed at a real file hangs rather than failing. `history_store_test.dart`
/// is where the store itself is tested, on real bytes; these tests are about
/// the payload half of the screen and need the series only to be absent.
class _NoHistory implements HistorySource {
  const _NoHistory();

  @override
  Future<NetWorthHistory> load() async => NetWorthHistory.empty;
}

void main() {
  testWidgets('the fixture seam loads a bundled payload end to end', (tester) async {
    await tester.pumpWidget(
      localized(
        HomePage(
          source: FixtureSnapshotSource(knownFixture, bundle: StringAssetBundle.ofFixtures()),
          historySource: const _NoHistory(),
          clock: () => DateTime.utc(2026, 9, 15, 12),
        ),
        scaffold: false,
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text(r'$42,500.00'), findsOneWidget);
    expect(find.text('as of Sep 14, 2026, 20:15 UTC'), findsOneWidget);
  });

  testWidgets('while loading there is no headline at all, bare or otherwise', (tester) async {
    // "No intermediate state renders a bare headline" is the criterion. The
    // strongest form of it is that the amount and its age arrive together or not
    // at all — there is no frame in which one is on screen without the other.
    await tester.pumpWidget(localized(HomePage(source: _PendingSource(), historySource: const _NoHistory()), scaffold: false));
    await tester.pump();

    expect(find.byType(Headline), findsNothing);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.textContaining(r'$'), findsNothing);
  });

  testWidgets('an unreadable payload is a refusal, not a number', (tester) async {
    await tester.pumpWidget(localized(HomePage(source: _FailingSource(), historySource: const _NoHistory()), scaffold: false));
    await tester.pumpAndSettle();

    expect(find.byType(Headline), findsNothing);
    expect(find.text("couldn't read the published snapshot"), findsOneWidget);
  });

  testWidgets('and the failure never shows the owner the exception text', (tester) async {
    // PR #77 re-review blocker 2. The screen rendered `'\${snapshot.error}'`
    // under the summary, so `_FailingSource`'s `StateError` put "Bad state: no
    // payload" in front of the owner — past the i18n layer, in a shape nobody
    // chose, and with no bound on what an exception's message might contain.
    //
    // Asserted on the whole rendered surface rather than on the one widget that
    // used to carry it: the defect is internal text reaching the screen, not one
    // particular `Text`.
    await tester.pumpWidget(localized(HomePage(source: _FailingSource(), historySource: const _NoHistory()), scaffold: false));
    await tester.pumpAndSettle();

    final rendered = renderedText(tester, find.byType(HomePage));
    expect(rendered, isNotEmpty);
    for (final line in rendered) {
      for (final leak in ['Bad state', 'no payload', 'Exception', 'Error']) {
        expect(
          line,
          isNot(contains(leak)),
          reason: 'internal error text reached the screen: "\$line"',
        );
      }
    }
  });

  testWidgets('the app boots', (tester) async {
    await tester.pumpWidget(
      NetWorthApp(
        source: FixtureSnapshotSource(mixedFixture, bundle: StringAssetBundle.ofFixtures()),
        historySource: const _NoHistory(),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byType(Headline), findsOneWidget);
  });
}
