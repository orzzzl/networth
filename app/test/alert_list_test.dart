import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/net_worth_history.dart';
import 'package:networth_app/src/domain/payload_alert.dart';
import 'package:networth_app/src/domain/phone_payload.dart';
import 'package:networth_app/src/ui/alert_list.dart';
import 'package:networth_app/src/ui/history_curve.dart';
import 'package:networth_app/src/ui/snapshot_view.dart';

import 'fixtures.dart';

/// Alerts parsed through the shipped parser rather than built by hand, so a test
/// cannot pass against a shape `publisher.py` never sends.
List<PayloadAlert> alertsOf(List<Map<String, Object?>> rows) => PhonePayload.fromJsonString(
      jsonEncode({
        ...jsonDecode(readFixture(knownFixture)) as Map<String, Object?>,
        'alerts': rows,
      }),
    ).alerts;

Map<String, Object?> row({
  required String kind,
  String subjectKind = 'ITEM',
  required int subjectId,
  String message = 'the host said something',
}) =>
    {
      'kind': kind,
      'subject': {'kind': subjectKind, 'id': subjectId},
      'message': message,
      'prompt': false,
    };

Future<void> pumpAlerts(WidgetTester tester, List<PayloadAlert> alerts) =>
    tester.pumpWidget(localized(AlertList(alerts: alerts)));

void main() {
  testWidgets('an empty set renders nothing at all', (tester) async {
    // Not an empty heading: a section title that is always there teaches the
    // owner that it means nothing, which is the one habit section 11's channel
    // cannot afford.
    await pumpAlerts(tester, const []);

    expect(find.text('Action needed'), findsNothing);
    expect(find.byType(Container), findsNothing);
    expect(tester.getSize(find.byType(AlertList)), Size.zero);
  });

  testWidgets('one subject reads as one, several read as a number', (tester) async {
    await pumpAlerts(tester, alertsOf([row(kind: 'NEEDS_REAUTH', subjectId: 1)]));
    expect(
      find.text('A connection needs you to sign in again before it can update.'),
      findsOneWidget,
    );

    await pumpAlerts(tester, alertsOf([
      row(kind: 'NEEDS_REAUTH', subjectId: 1),
      row(kind: 'NEEDS_REAUTH', subjectId: 2),
    ]));
    expect(
      find.text('2 connections need you to sign in again before they can update.'),
      findsOneWidget,
    );
  });

  testWidgets('each of the five kinds has copy of its own', (tester) async {
    // The point is not the wording, it is that no two kinds share a sentence.
    // REVOKED and NEEDS_REAUTH are the pair that matters: signing in again does
    // not fix a revoked connection, so a shared line sends the owner to the
    // wrong screen half the time.
    final rendered = <AlertKind, String>{};
    for (final kind in AlertKind.values) {
      await pumpAlerts(tester, alertsOf([
        row(
          kind: kind.wire,
          subjectKind: kind == AlertKind.needsReauth || kind == AlertKind.revoked
              ? 'ITEM'
              : 'ACCOUNT',
          subjectId: 1,
        ),
      ]));
      final texts = renderedText(tester, find.byType(AlertList))
        ..removeWhere((text) => text == 'Action needed');
      expect(texts, hasLength(1), reason: kind.wire);
      rendered[kind] = texts.single;
    }

    expect(rendered.values.toSet(), hasLength(AlertKind.values.length));
    expect(rendered[AlertKind.needsReauth], isNot(rendered[AlertKind.revoked]));
  });

  testWidgets('a kind this build does not know renders the host sentence', (tester) async {
    // Section 11 added a fifth kind inside schema_version 1, so this is the
    // documented history of the field rather than a hypothetical. The row must
    // not be dropped: on the only alerting surface there is, a silently shorter
    // list reads as a healthier portfolio.
    await pumpAlerts(tester, alertsOf([
      row(
        kind: 'CUSTODIAN_MERGED',
        subjectKind: 'ACCOUNT',
        subjectId: 4,
        message: 'Two of your accounts were merged by the institution.',
      ),
    ]));

    expect(find.text('Action needed'), findsOneWidget);
    expect(find.text('Two of your accounts were merged by the institution.'), findsOneWidget);
  });

  testWidgets('a known kind never renders the host sentence', (tester) async {
    // The inverse of the test above, and the reason the app has an ARB at all:
    // `message` is English the daemon hard-codes, and rendering it for a kind
    // the app has copy for would be an un-localizable string on screen that
    // nothing in the ARB describes.
    await pumpAlerts(tester, alertsOf([
      row(kind: 'FROZEN_DATA', subjectKind: 'ACCOUNT', subjectId: 1, message: 'host prose'),
    ]));

    expect(find.text('host prose'), findsNothing);
    expect(find.textContaining('stopped changing'), findsOneWidget);
  });

  testWidgets('an unhealthy state is rendered as one, not as another grey row', (
    tester,
  ) async {
    // Section 11: "an unhealthy state must be impossible to miss on open". The
    // colours are asserted through the theme rather than as literals, so this
    // pins "rendered as a warning" and not one particular palette.
    await pumpAlerts(tester, alertsOf([row(kind: 'NEEDS_REAUTH', subjectId: 1)]));

    final theme = Theme.of(tester.element(find.byType(AlertList)));
    final box = tester.widget<Container>(find.descendant(
      of: find.byType(AlertList),
      matching: find.byType(Container),
    ));

    expect(
      (box.decoration! as BoxDecoration).color,
      theme.colorScheme.errorContainer,
    );
    for (final text in tester.widgetList<Text>(
      find.descendant(of: find.byType(AlertList), matching: find.byType(Text)),
    )) {
      expect(text.style?.color, theme.colorScheme.onErrorContainer);
    }
  });

  testWidgets('on the screen it sits above the curve, not below it', (tester) async {
    // The requirement a scroll position can defeat. Below the curve this block
    // is under the fold on a phone, and "impossible to miss on open" is the one
    // thing on this screen that placement alone can break.
    await tester.pumpWidget(
      localized(
        SnapshotView(
          payload: loadFixture(alertsOpenFixture),
          history: NetWorthHistory.empty,
          recordingFailed: false,
          deviceNow: DateTime.utc(2026, 9, 15, 12),
          // Placement is the subject here, so the clock is held neutral rather
          // than left to a default — `continuity` has none, by design.
          continuity: trustedClock,
        ),
      ),
    );

    expect(find.text('Action needed'), findsOneWidget);
    expect(
      tester.getTopLeft(find.byType(AlertList)).dy,
      lessThan(tester.getTopLeft(find.byType(HistoryCurve)).dy),
    );
  });

  testWidgets('a healthy payload draws no alert block on the screen', (tester) async {
    await tester.pumpWidget(
      localized(
        SnapshotView(
          payload: loadFixture(knownFixture),
          history: NetWorthHistory.empty,
          recordingFailed: false,
          deviceNow: DateTime.utc(2026, 9, 15, 12),
          // An empty alert set is the subject; a distrusted clock changes the
          // copy row above and would leave this asserting two things at once.
          continuity: trustedClock,
        ),
      ),
    );

    expect(find.text('Action needed'), findsNothing);
    expect(tester.getSize(find.byType(AlertList)), Size.zero);
  });

  testWidgets('the shipped open set draws one row per kind, not one per alert', (
    tester,
  ) async {
    // Three alerts, two kinds. Three identical re-auth sentences would read as a
    // rendering bug rather than as two accounts, and the payload gives this side
    // nothing to tell the two subjects apart with.
    await pumpAlerts(tester, loadFixture(alertsOpenFixture).alerts);

    final texts = renderedText(tester, find.byType(AlertList))
      ..removeWhere((text) => text == 'Action needed');

    expect(texts, hasLength(2));
    expect(texts.first, contains('2 connections'));
    expect(texts.last, contains("isn't in your total yet"));
  });
}
