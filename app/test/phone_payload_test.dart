import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/snapshot_source.dart';
import 'package:networth_app/src/domain/copy_freshness.dart';
import 'package:networth_app/src/domain/payload_format_exception.dart';
import 'package:networth_app/src/domain/phone_payload.dart';

import 'fixtures.dart';

String _fixtureWith(String assetPath, Map<String, Object?> overrides) =>
    jsonEncode({...jsonDecode(readFixture(assetPath)) as Map<String, Object?>, ...overrides});

void main() {
  group('the header this build reads', () {
    test('every shipped fixture parses', () {
      for (final fixture in allFixtures) {
        final payload = loadFixture(fixture);
        expect(payload.schemaVersion, PhonePayload.supportedSchemaVersion, reason: fixture);
        expect(payload.publishInterval, const Duration(seconds: 86400), reason: fixture);
        expect(payload.grace, const Duration(seconds: 21600), reason: fixture);
      }
    });

    test('the cadence is read off the wire, not hardcoded', () {
      final payload = PhonePayload.fromJsonString(
        _fixtureWith(knownFixture, {'publish_interval_seconds': 3600, 'grace_seconds': 600}),
      );

      expect(payload.publishInterval, const Duration(seconds: 3600));
      expect(
        staleAfter(
          publishedAt: payload.publishedAt,
          publishInterval: payload.publishInterval,
          grace: payload.grace,
        ),
        DateTime.utc(2026, 9, 15, 5, 10),
      );
    });

    test('a newer schema is refused rather than misread', () {
      // The daemon and the phone share one contract, versioned explicitly; an
      // older app must refuse a newer payload rather than guess at it.
      expect(
        () => PhonePayload.fromJsonString(_fixtureWith(knownFixture, {'schema_version': '2'})),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('an unknown connection state is refused rather than treated as OK', () {
      expect(
        () => PhonePayload.fromJsonString(
          _fixtureWith(knownFixture, {'connection_state': 'PROBABLY_FINE'}),
        ),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('text that is not JSON is a refusal, not a crash on a null', () {
      expect(
        () => PhonePayload.fromJsonString('<html>a captive portal</html>'),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('a JSON document that is not an object', () {
      expect(
        () => PhonePayload.fromJsonString('[]'),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('a missing total', () {
      expect(
        () => PhonePayload.fromJsonString(_fixtureWith(knownFixture, {'total': null})),
        throwsA(isA<PayloadFormatException>()),
      );
    });
  });

  group('the fixture seam', () {
    test('loads through an asset bundle', () async {
      final source = FixtureSnapshotSource(
        mixedFixture,
        bundle: StringAssetBundle.ofFixtures(),
      );

      final payload = await source.load();

      expect(payload.seq, '42');
      expect(payload.connectionState, ConnectionDisplayState.waiting);
    });

    test('a missing asset fails loudly', () {
      final source = FixtureSnapshotSource(
        'assets/fixtures/nope.json',
        bundle: StringAssetBundle.ofFixtures(),
      );

      expect(source.load(), throwsA(isA<Object>()));
    });
  });
}
