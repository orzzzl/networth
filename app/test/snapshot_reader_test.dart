
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/snapshot_reader.dart';
import 'package:networth_app/src/data/snapshot_transport.dart';
import 'package:networth_app/src/domain/phone_payload.dart';
import 'package:networth_app/src/pairing/pairing_provision.dart';
import 'package:networth_app/src/pairing/pairing_vault.dart';

import 'fixtures.dart';

/// Builds a reader whose transport talks to [route], and returns its one
/// outcome. [store] holds whatever the phone has been paired with.
Future<SnapshotReadOutcome> _read({
  TestRoute? route,
  SecureStringStore? store,
  List<PairingProvision>? seen,
}) async {
  final port = route == null ? 0 : await route.start();
  if (route != null) {
    addTearDown(route.stop);
  }
  final reader = PairedSnapshotReader(
    vault: PairingVault(store: store ?? MemoryPairingStore(encoded)),
    openTransport: (provision) {
      seen?.add(provision);
      return SnapshotTransport(
        host: InternetAddress.loopbackIPv4.address,
        port: port,
        deadline: const Duration(seconds: 5),
      );
    },
  );
  return reader.read();
}

void main() {
  group('the pairing the phone holds', () {
    test('an unpaired phone is not a failed fetch, and fetches nothing', () async {
      final seen = <PairingProvision>[];

      final outcome = await _read(store: MemoryPairingStore(), seen: seen);

      expect(outcome, isA<SnapshotReadNotPaired>());
      // The distinction is only worth anything if nothing was attempted: a
      // `404` from a host we asked would be a different story about the same
      // phone.
      expect(seen, isEmpty);
    });

    test('protected storage that throws is not "you are not paired"', () async {
      final seen = <PairingProvision>[];

      final outcome = await _read(store: FailingPairingStore(), seen: seen);

      expect(outcome, isA<SnapshotReadPairingUnreadable>());
      expect(seen, isEmpty);
    });

    test('a stored bundle the parser refuses is also unreadable, not absent', () async {
      // The vault stores one string and parses it on the way out, so this is
      // what a truncated write leaves behind. Telling the owner he is unpaired
      // here would invite a re-pair, which rotates the key on a working host.
      final outcome = await _read(store: MemoryPairingStore(encoded.substring(0, 30)));

      expect(outcome, isA<SnapshotReadPairingUnreadable>());
    });

    test('the transport is built from the provision, on the daemon port', () {
      final transport = PairedSnapshotReader.transportFor(
        PairingProvision.parse(encoded),
      );

      expect(transport.host, tailnetName);
      expect(transport.port, snapshotServePort);
    });

    test('the provision handed to the transport is the stored one', () async {
      final seen = <PairingProvision>[];

      await _read(route: answering(HttpStatus.notFound), seen: seen);

      expect(seen, hasLength(1));
      expect(seen.single.pairingId, pairingId);
      expect(seen.single.tailnetName, tailnetName);
    });
  });

  group('the host delivered no body', () {
    test('404 arrives as the transport named it: no publication', () async {
      final outcome = await _read(route: answering(HttpStatus.notFound));

      expect(outcome, isA<SnapshotReadNotDelivered>());
      expect(
        (outcome as SnapshotReadNotDelivered).transport,
        isA<SnapshotNoPublication>(),
      );
    });

    test('503 stays "the host is up and broken"', () async {
      final outcome = await _read(
        route: answering(HttpStatus.serviceUnavailable),
      );

      expect(
        (outcome as SnapshotReadNotDelivered).transport,
        isA<SnapshotSourceUnavailable>(),
      );
    });

    test('a status the route does not define keeps its number', () async {
      final outcome = await _read(route: answering(418));

      final transport = (outcome as SnapshotReadNotDelivered).transport;
      expect((transport as SnapshotUnexpectedStatus).statusCode, 418);
    });

    test('a captive portal is a transport fault, never a bad envelope', () async {
      // A `200` of HTML. Reported as the publisher's fault it would blame the
      // host for the coffee shop; the transport's `notThisRoute` is the truth.
      final outcome = await _read(
        route: serving('<html>sign in</html>', contentType: ContentType.html),
      );

      final transport = (outcome as SnapshotReadNotDelivered).transport;
      expect(
        (transport as SnapshotTransportFailure).fault,
        SnapshotTransportFault.notThisRoute,
      );
    });

    test('a received body cannot be built into a non-delivery', () {
      // The one invariant `SnapshotReadNotDelivered` documents and does not
      // carry in its type. Without this, the only thing standing behind that
      // paragraph is the narrowing in `read`.
      expect(
        () => SnapshotReadNotDelivered('p', const SnapshotBodyReceived('{}')),
        throwsA(isA<AssertionError>()),
      );
    });
  });

  group('bytes arrived and are not this phone\'s payload', () {
    test('a body that is not an envelope at all', () async {
      final outcome = await _read(route: serving('{"not":"an envelope"}'));

      expect(
        (outcome as SnapshotReadRejected).reason,
        SnapshotRejection.notAnEnvelope,
      );
    });

    test('an envelope published under another pairing says so', () async {
      // What a phone left on an old pairing receives: `serve.py` selects the
      // active envelope with no reference to who asked, so the bytes it gets
      // are the *current* pairing's. Reported as `notAuthentic` this reads as
      // an attack; the answer is to pair again.
      final outcome = await _read(
        route: serving(envelopeFixture('known_envelope.json')),
      );

      expect(
        (outcome as SnapshotReadRejected).reason,
        SnapshotRejection.otherPairing,
      );
    });

    test('a superseded pairing is named even when the key is wrong too', () async {
      // The case that makes the *order* load-bearing rather than a detail. A
      // phone left on an old pairing has both the wrong id and the wrong key,
      // so checking the header after the tag would report `notAuthentic` — the
      // alarming answer — for the ordinary event of having re-paired
      // elsewhere. Checking it first is what makes the advice *pair again*.
      final outcome = await _read(
        route: serving(envelopeFixture('known_envelope.json')),
        store: MemoryPairingStore(encodedWithOtherKey),
      );

      expect(
        (outcome as SnapshotReadRejected).reason,
        SnapshotRejection.otherPairing,
      );
    });

    test('the right pairing and the wrong key does not verify', () async {
      final outcome = await _read(
        route: serving(envelopeFixture('paired_envelope.json')),
        store: MemoryPairingStore(encodedWithOtherKey),
      );

      expect(
        (outcome as SnapshotReadRejected).reason,
        SnapshotRejection.notAuthentic,
      );
    });

    test('an authentic envelope whose two headers disagree is refused', () async {
      final outcome = await _read(
        route: serving(envelopeFixture('paired_seq_mismatch_envelope.json')),
      );

      expect(
        (outcome as SnapshotReadRejected).reason,
        SnapshotRejection.headerDisagreement,
      );
    });

    test('an authentic envelope with no total is a payload fault', () async {
      final outcome = await _read(
        route: serving(envelopeFixture('paired_no_total_envelope.json')),
      );

      expect(
        (outcome as SnapshotReadRejected).reason,
        SnapshotRejection.payloadNotReadable,
      );
    });

  });

  group('the whole path, end to end', () {
    test('a paired phone opens exactly the shipped fixture', () async {
      // Compared against `assets/fixtures/known.json` on disk rather than
      // against values restated here, for the reason `fixtures.dart` gives: a
      // test that checks a copy of the fixture passes after someone edits the
      // fixture. `paired_envelope.json` is that document with one field
      // changed, so `pairing_id` is the one thing expected to differ — and it
      // is asserted against the pairing the phone holds, which is the whole
      // point of the join.
      final expected = loadFixture(knownFixture);

      final outcome = await _read(
        route: serving(envelopeFixture('paired_envelope.json')),
      );

      expect(outcome, isA<SnapshotReadPayload>());
      final payload = (outcome as SnapshotReadPayload).payload;
      expect(payload.pairingId, pairingId);
      expect(payload.pairingId, isNot(expected.pairingId));
      expect(payload.schemaVersion, expected.schemaVersion);
      expect(payload.seq, expected.seq);
      expect(payload.publishedAt, expected.publishedAt);
      expect(payload.publishInterval, expected.publishInterval);
      expect(payload.grace, expected.grace);
      expect(payload.connectionState, expected.connectionState);
      expect(payload.total.amount, expected.total.amount);
      expect(payload.total.assets, expected.total.assets);
      expect(payload.total.liabilities, expected.total.liabilities);
      expect(payload.total.staticAccountCount, expected.total.staticAccountCount);
      expect(payload.total.isComplete, expected.total.isComplete);
      // `DatedTotal` is sealed on the age state, so the variant *is* the
      // `age_state` the host sent.
      expect(payload.total.runtimeType, expected.total.runtimeType);
    });

    test('the pairing an attempt names is the one it was made under', () async {
      // **The phone's, never the envelope's**, and the `otherPairing` case is
      // where a plausible implementation gets it wrong: `known_envelope.json`
      // is published under `fixture-pairing` while this phone holds
      // `pairingId`, so an outcome that reported the envelope's id would file
      // this attempt's §9.1 facts under a pairing the phone has never had.
      final rejected = await _read(
        route: serving(envelopeFixture('known_envelope.json')),
      );
      final delivered = await _read(route: answering(HttpStatus.notFound));
      final opened = await _read(
        route: serving(envelopeFixture('paired_envelope.json')),
      );

      expect((rejected as SnapshotAttempted).pairingId, pairingId);
      expect((delivered as SnapshotAttempted).pairingId, pairingId);
      expect((opened as SnapshotAttempted).pairingId, pairingId);
      // And the envelope really did claim a different one, so the assertion
      // above is discriminating rather than two names that happen to match.
      expect(
        PhonePayload.fromJsonString(readFixture(knownFixture)).pairingId,
        isNot(pairingId),
      );
    });

    test('an attempt that was never made names no pairing', () async {
      // The other half of the split: these two are exactly the states in which
      // no fetch happened, so there is nothing to file and no id to file it
      // under. A `SnapshotAttempted` here would be a record of an attempt that
      // does not exist.
      expect(await _read(store: MemoryPairingStore()), isNot(isA<SnapshotAttempted>()));
      expect(await _read(store: FailingPairingStore()), isNot(isA<SnapshotAttempted>()));
    });

    test('every attempt reads the vault again, so a rotation takes effect', () async {
      // Nothing caches the key. A reader that outlived a re-pair and kept using
      // the old one would fail every fetch after it with `notAuthentic`.
      final store = MemoryPairingStore(encodedWithOtherKey);
      final route = serving(envelopeFixture('paired_envelope.json'));
      final port = await route.start();
      addTearDown(route.stop);
      final reader = PairedSnapshotReader(
        vault: PairingVault(store: store),
        openTransport: (_) => SnapshotTransport(
          host: InternetAddress.loopbackIPv4.address,
          port: port,
          deadline: const Duration(seconds: 5),
        ),
      );

      final before = await reader.read();
      store.stored = encoded;
      final after = await reader.read();

      expect(
        (before as SnapshotReadRejected).reason,
        SnapshotRejection.notAuthentic,
      );
      expect(after, isA<SnapshotReadPayload>());
    });
  });
}
