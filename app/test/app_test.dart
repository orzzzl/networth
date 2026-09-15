import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/main.dart';
import 'package:networth_app/src/data/snapshot_source.dart';
import 'package:networth_app/src/domain/phone_payload.dart';
import 'package:networth_app/src/ui/headline.dart';
import 'package:networth_app/src/ui/home_page.dart';

import 'fixtures.dart';

/// A source that never completes, so the loading frame can be inspected.
class _PendingSource implements SnapshotSource {
  @override
  Future<PhonePayload> load() => Completer<PhonePayload>().future;
}

class _FailingSource implements SnapshotSource {
  @override
  Future<PhonePayload> load() async => throw StateError('no payload');
}

void main() {
  testWidgets('the fixture seam loads a bundled payload end to end', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: HomePage(
          source: FixtureSnapshotSource(knownFixture, bundle: StringAssetBundle.ofFixtures()),
          clock: () => DateTime.utc(2026, 9, 15, 12),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text(r'$42,500.00'), findsOneWidget);
    expect(find.text('as of 14 Sep 2026, 20:15 UTC'), findsOneWidget);
  });

  testWidgets('while loading there is no headline at all, bare or otherwise', (tester) async {
    // "No intermediate state renders a bare headline" is the criterion. The
    // strongest form of it is that the amount and its age arrive together or not
    // at all — there is no frame in which one is on screen without the other.
    await tester.pumpWidget(MaterialApp(home: HomePage(source: _PendingSource())));
    await tester.pump();

    expect(find.byType(Headline), findsNothing);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.textContaining(r'$'), findsNothing);
  });

  testWidgets('an unreadable payload is a refusal, not a number', (tester) async {
    await tester.pumpWidget(MaterialApp(home: HomePage(source: _FailingSource())));
    await tester.pumpAndSettle();

    expect(find.byType(Headline), findsNothing);
    expect(find.text("couldn't read the published snapshot"), findsOneWidget);
  });

  testWidgets('the app boots', (tester) async {
    await tester.pumpWidget(
      NetWorthApp(
        source: FixtureSnapshotSource(mixedFixture, bundle: StringAssetBundle.ofFixtures()),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byType(Headline), findsOneWidget);
  });
}
