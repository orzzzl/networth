import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/payload_alert.dart';
import 'package:networth_app/src/domain/payload_format_exception.dart';
import 'package:networth_app/src/domain/phone_payload.dart';

import 'fixtures.dart';

/// A payload with [alerts] in place of whatever the fixture carried.
String _withAlerts(List<Object?> alerts) => jsonEncode({
      ...jsonDecode(readFixture(knownFixture)) as Map<String, Object?>,
      'alerts': alerts,
    });

Map<String, Object?> _alert({
  String kind = 'NEEDS_REAUTH',
  String subjectKind = 'ITEM',
  Object? subjectId = 1,
  String message = 'This connection needs you to sign in again before it can update.',
  Object? prompt = false,
}) =>
    {
      'kind': kind,
      'subject': {'kind': subjectKind, 'id': subjectId},
      'message': message,
      'prompt': prompt,
    };

List<PayloadAlert> _parse(List<Object?> alerts) =>
    PhonePayload.fromJsonString(_withAlerts(alerts)).alerts;

void main() {
  group('the wire shape', () {
    test('every shipped fixture carries an alerts array', () {
      // The field is required, so this is the check that says so at the fixture
      // rather than three tests later — the same role the zone check on
      // `published_at` plays in `phone_payload_test.dart`.
      for (final fixture in allFixtures) {
        final body = jsonDecode(readFixture(fixture)) as Map<String, Object?>;
        expect(body['alerts'], isA<List<Object?>>(), reason: fixture);
      }
    });

    test('the shipped open set parses into what the host sent', () {
      final payload = loadFixture(alertsOpenFixture);

      expect(payload.alerts, hasLength(3));
      expect(
        payload.alerts.map((alert) => alert.kind).toList(),
        [AlertKind.needsReauth, AlertKind.needsReauth, AlertKind.pendingReconciliation],
      );
      expect(payload.alerts.first.subject.scope, AlertSubjectScope.item);
      expect(payload.alerts.first.subject.id, 1);
      expect(payload.alerts.last.subject.scope, AlertSubjectScope.account);
    });

    test('an empty open set is empty, and the other fixtures are', () {
      for (final fixture in [knownFixture, mixedFixture, staticOnlyFixture]) {
        expect(loadFixture(fixture).alerts, isEmpty, reason: fixture);
      }
    });

    test('a missing alerts field is refused, not read as "nothing is wrong"', () {
      // The two are one careless decode apart and they mean opposite things.
      // Section 11 makes this screen the only place an alert can ever be seen,
      // so a default of `[]` would be silently right almost always and wrong in
      // the only case the field exists for.
      final body = jsonDecode(readFixture(knownFixture)) as Map<String, Object?>
        ..remove('alerts');

      expect(
        () => PhonePayload.fromJsonString(jsonEncode(body)),
        throwsA(
          isA<PayloadFormatException>().having((e) => e.message, 'message', contains('alerts')),
        ),
      );
    });

    test('alerts must be an array, and a row must be an object', () {
      expect(
        () => PhonePayload.fromJsonString(jsonEncode({
          ...jsonDecode(readFixture(knownFixture)) as Map<String, Object?>,
          'alerts': {'kind': 'NEEDS_REAUTH'},
        })),
        throwsA(isA<PayloadFormatException>()),
      );
      expect(() => _parse(['NEEDS_REAUTH']), throwsA(isA<PayloadFormatException>()));
    });

    test('the refusal names which row was wrong', () {
      // Three alerts arrive together routinely; "alerts is malformed" would send
      // whoever reads the log looking through all of them.
      expect(
        () => _parse([_alert(), _alert(), _alert(subjectId: 'three')]),
        throwsA(
          isA<PayloadFormatException>()
              .having((e) => e.message, 'message', contains('alerts[2].subject.id')),
        ),
      );
    });

    test('a subject id that is not an integer is refused', () {
      // `Alert.subject_id` is an `int` on the host — a local row id. A string
      // here means something other than `publisher.py` built this payload.
      expect(() => _parse([_alert(subjectId: '1')]), throwsA(isA<PayloadFormatException>()));
      expect(() => _parse([_alert(subjectId: 1.0)]), throwsA(isA<PayloadFormatException>()));
      expect(() => _parse([_alert(subjectId: null)]), throwsA(isA<PayloadFormatException>()));
    });

    test('an unknown subject scope is refused, unlike an unknown kind', () {
      // On the host this is not a vocabulary: `_alert` derives it from the
      // boolean `AlertKind.is_item_scoped`, so a third value means the contract
      // has broken rather than moved on.
      expect(
        () => _parse([_alert(subjectKind: 'PORTFOLIO')]),
        throwsA(
          isA<PayloadFormatException>()
              .having((e) => e.message, 'message', contains('subject.kind')),
        ),
      );
    });

    test('prompt is validated even though nothing reads it', () {
      // It is checked and deliberately not stored: section 11 gives it one
      // consumer, a background local notification this build does not have. The
      // check is what turns a change to the field into a red test for whoever
      // builds that slice.
      expect(() => _parse([_alert(prompt: 'yes')]), throwsA(isA<PayloadFormatException>()));
      expect(() => _parse([_alert(prompt: null)]), throwsA(isA<PayloadFormatException>()));
      expect(_parse([_alert(prompt: true)]), hasLength(1));
    });

    test('the parsed list cannot be mutated by its holder', () {
      final alerts = _parse([_alert()]);

      expect(() => alerts.add(alerts.first), throwsUnsupportedError);
    });
  });

  group('an unknown kind', () {
    test('is kept, with its wire name and the host sentence', () {
      // Section 11 added SHARE_COUNT_UNCONFIRMED as a fifth kind INSIDE
      // schema_version 1, so a host newer than the app is not hypothetical here.
      final alerts = _parse([
        _alert(kind: 'CUSTODIAN_MERGED', message: 'Two of your accounts were merged.'),
      ]);

      expect(alerts.single.kind, isNull);
      expect(alerts.single.wireKind, 'CUSTODIAN_MERGED');
      expect(alerts.single.hostMessage, 'Two of your accounts were merged.');
    });

    test('does not take the whole payload down with it', () {
      // The alternative to tolerating it is refusing the payload, which would
      // discard the total and the alerts that arrived beside it.
      final payload = PhonePayload.fromJsonString(
        _withAlerts([_alert(kind: 'CUSTODIAN_MERGED'), _alert()]),
      );

      expect(payload.alerts, hasLength(2));
      expect(payload.total, isNotNull);
    });

    test('is not confused with a kind spelled wrong in case', () {
      // The wire values are upper-case; a lower-case match would be this parser
      // inventing a tolerance the contract does not have.
      expect(_parse([_alert(kind: 'needs_reauth')]).single.kind, isNull);
    });
  });

  group('summarizing for the screen', () {
    test('counts distinct subjects, not rows', () {
      // The host keeps at most one open alert per kind per subject, so a
      // duplicate is a host defect — and reporting "3 connections" about two
      // would be this side amplifying it.
      final summaries = summarizeAlerts(_parse([
        _alert(subjectId: 1),
        _alert(subjectId: 2),
        _alert(subjectId: 2),
      ]));

      expect(summaries, hasLength(1));
      expect(summaries.single.subjectCount, 2);
    });

    test('the same id under two scopes is two subjects', () {
      // Item 1 and account 1 are different rows in different tables. Counting
      // the id alone would collapse them.
      final summaries = summarizeAlerts([
        ..._parse([_alert(kind: 'FROZEN_DATA', subjectKind: 'ITEM', subjectId: 1)]),
        ..._parse([_alert(kind: 'FROZEN_DATA', subjectKind: 'ACCOUNT', subjectId: 1)]),
      ]);

      expect(summaries.single.subjectCount, 2);
    });

    test('groups by kind and orders by the host vocabulary, not payload order', () {
      // Payload order is the database's. Two publications with the same open set
      // must not be able to draw two different screens.
      final summaries = summarizeAlerts(_parse([
        _alert(kind: 'SHARE_COUNT_UNCONFIRMED', subjectKind: 'ACCOUNT', subjectId: 9),
        _alert(kind: 'REVOKED', subjectId: 4),
        _alert(kind: 'NEEDS_REAUTH', subjectId: 1),
      ]));

      expect(
        summaries.map((summary) => summary.kind).toList(),
        [AlertKind.needsReauth, AlertKind.revoked, AlertKind.shareCountUnconfirmed],
      );
    });

    test('unknown kinds sort last, and among themselves deterministically', () {
      final summaries = summarizeAlerts(_parse([
        _alert(kind: 'ZEBRA'),
        _alert(kind: 'APPLE'),
        _alert(kind: 'FROZEN_DATA', subjectKind: 'ACCOUNT', subjectId: 7),
      ]));

      expect(
        summaries.map((summary) => summary.wireKind).toList(),
        ['FROZEN_DATA', 'APPLE', 'ZEBRA'],
      );
    });

    test('an unknown kind carries its distinct messages, de-duplicated in order', () {
      final summaries = summarizeAlerts(_parse([
        _alert(kind: 'CUSTODIAN_MERGED', subjectId: 1, message: 'first'),
        _alert(kind: 'CUSTODIAN_MERGED', subjectId: 2, message: 'second'),
        _alert(kind: 'CUSTODIAN_MERGED', subjectId: 3, message: 'first'),
      ]));

      expect(summaries.single.hostMessages, ['first', 'second']);
      expect(summaries.single.subjectCount, 3);
    });

    test('an empty set summarizes to nothing at all', () {
      expect(summarizeAlerts(const []), isEmpty);
    });

    test('the shipped open set becomes two rows', () {
      final summaries = summarizeAlerts(loadFixture(alertsOpenFixture).alerts);

      expect(summaries.map((summary) => summary.kind).toList(), [
        AlertKind.needsReauth,
        AlertKind.pendingReconciliation,
      ]);
      expect(summaries.first.subjectCount, 2);
      expect(summaries.last.subjectCount, 1);
    });
  });
}
