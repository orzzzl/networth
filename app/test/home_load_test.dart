import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/fetch_diagnostics_store.dart';
import 'package:networth_app/src/data/held_copy_store.dart';
import 'package:networth_app/src/data/home_load.dart';
import 'package:networth_app/src/data/seq_baseline_store.dart';
import 'package:networth_app/src/data/snapshot_reader.dart';
import 'package:networth_app/src/data/snapshot_refresh.dart';
import 'package:networth_app/src/data/snapshot_transport.dart';
import 'package:networth_app/src/domain/fetch_diagnostics.dart';
import 'package:networth_app/src/domain/held_copy.dart';
import 'package:networth_app/src/domain/seq_baseline.dart';
import 'package:networth_app/src/pairing/pairing_vault.dart';

import 'fixtures.dart';

/// A second pairing, for the rotation case. Same host and key, different id —
/// the id is the scope, and the scope is what that test is about.
const otherPairingId = '00000000-0000-4000-8000-000000000003';
const encodedOtherPairing = 'networth-pairing:v1:$otherPairingId:$encodedKey:$tailnetName';

/// Stores the copy the refresh accepted and then reports holding nothing.
///
/// The one seam a screen that trusted `RefreshAccepted` could not tell from a
/// screen that re-read the store: with the real store those two agree, which is
/// exactly why agreement proves nothing about which one was asked.
class _HoldsButReportsAbsent implements HeldCopyStore {
  _HoldsButReportsAbsent(this._inner);

  final HeldCopyStore _inner;
  bool held = false;

  @override
  Future<void> hold(String source) async {
    held = true;
    await _inner.hold(source);
  }

  @override
  Future<HeldCopyState> read(String pairingId) async => const HeldCopyAbsent();
}

/// Protected storage whose answer changes between the two reads of one load.
///
/// A pairing rotating mid-load is the real event this stands for: the owner
/// re-pairs while the app is open. Which of the two answers the screen is scoped
/// to is the whole question, and a store that returns one value cannot ask it.
class _RotatingPairingStore implements SecureStringStore {
  _RotatingPairingStore(this._first, this._then);

  final String _first;
  final String _then;
  int reads = 0;

  @override
  Future<String?> read({required String key}) async => reads++ == 0 ? _first : _then;

  @override
  Future<void> write({required String key, required String value}) async {}

  @override
  Future<void> delete({required String key}) async {}
}

void main() {
  late Directory directory;
  late FileFetchDiagnosticsStore diagnostics;
  late FileSeqBaselineStore baselines;
  late FileHeldCopyStore heldCopies;

  final at = DateTime.utc(2026, 9, 24, 18);

  setUp(() async {
    directory = await Directory.systemTemp.createTemp('home-load-test');
    diagnostics = FileFetchDiagnosticsStore(
      open: () async => File('${directory.path}/${FileFetchDiagnosticsStore.fileName}'),
    );
    baselines = FileSeqBaselineStore(
      open: () async => File('${directory.path}/${FileSeqBaselineStore.fileName}'),
    );
    heldCopies = FileHeldCopyStore(
      open: () async => File('${directory.path}/${FileHeldCopyStore.fileName}'),
    );
  });

  tearDown(() async => directory.delete(recursive: true));

  /// One load, through the reader and refresher the app ships.
  ///
  /// The bytes arrive over a real loopback socket for `snapshot_refresh_test`'s
  /// reason: the outcomes this layer reads back should be the ones the transport
  /// and the stores actually produce, not a hand-built set standing where they go.
  Future<HomeLoad> loadHome({
    TestRoute? route,
    SecureStringStore? store,
    HeldCopyStore? copies,
  }) async {
    final port = route == null ? 0 : await route.start();
    if (route != null) {
      addTearDown(route.stop);
    }
    final vault = PairingVault(store: store ?? MemoryPairingStore(encoded));
    return HomeLoader(
      vault: vault,
      refresher: SnapshotRefresher(
        reader: PairedSnapshotReader(
          vault: vault,
          openTransport: (provision) => SnapshotTransport(
            host: InternetAddress.loopbackIPv4.address,
            port: port,
            deadline: const Duration(seconds: 5),
          ),
        ),
        diagnostics: diagnostics,
        baselines: baselines,
        heldCopies: copies ?? heldCopies,
        clock: () => at,
      ),
      heldCopies: copies ?? heldCopies,
      diagnostics: diagnostics,
      baselines: baselines,
    ).load();
  }

  TestRoute thePairedHost() => serving(envelopeFixture('paired_envelope.json'));

  /// Nothing is filed anywhere, under the pairing this phone would have had.
  Future<void> expectNothingFiled() async {
    expect(await diagnostics.read(pairingId), isA<DiagnosticsAbsent>());
    expect(await baselines.read(pairingId), isA<BaselineAbsent>());
    expect(await heldCopies.read(pairingId), isA<HeldCopyAbsent>());
  }

  test('a paired phone shows the copy it just fetched, with the records beside it', () async {
    final result = await loadHome(route: thePairedHost());

    expect(result, isA<HomePaired>());
    final paired = result as HomePaired;
    expect(paired.copy, isA<HeldCopyHeld>());
    expect(paired.diagnostics, isA<DiagnosticsHeld>());
    expect(paired.baseline, isA<BaselineHeld>());
  });

  test('a phone in no pairing says so, and files nothing under an invented scope', () async {
    expect(await loadHome(route: thePairedHost(), store: MemoryPairingStore()), isA<HomeNotPaired>());
    await expectNothingFiled();
  });

  test('a pairing that cannot be read is a different answer from no pairing', () async {
    // Never [HomeNotPaired]: that would send an owner whose device is fine to
    // re-pair, which is the one action that destroys the copy he still has.
    expect(
      await loadHome(route: thePairedHost(), store: FailingPairingStore()),
      isA<HomePairingUnreadable>(),
    );
    await expectNothingFiled();
  });

  test('the copy on screen is re-read from the store, not taken from the outcome', () async {
    final copies = _HoldsButReportsAbsent(heldCopies);

    final result = await loadHome(route: thePairedHost(), copies: copies);

    // Two-sided, and it has to be: the store reporting absence proves the screen
    // asked it only if the refresh really did accept and store a payload.
    expect(copies.held, isTrue, reason: 'the refresh did not store a copy, so this proves nothing');
    expect((result as HomePaired).copy, isA<HeldCopyAbsent>());
    expect(result.diagnostics, isA<DiagnosticsHeld>());
  });

  test('a paired phone holding no copy still carries why it has none', () async {
    // The failure §9.1 exists to surface: nothing to show, and a reason for it.
    // Reporting the absence without the reason is the silence that row is about.
    final result = await loadHome(route: answering(HttpStatus.notFound)) as HomePaired;

    expect(result.copy, isA<HeldCopyAbsent>());
    final diagnostics = result.diagnostics;
    expect(diagnostics, isA<DiagnosticsHeld>());
    expect((diagnostics as DiagnosticsHeld).diagnostics.foundNoPublication, isTrue);
  });

  test('a damaged copy is never reported as a missing one', () async {
    await File('${directory.path}/${FileHeldCopyStore.fileName}')
        .writeAsString('{not a payload');

    final result = await loadHome(store: MemoryPairingStore(encoded)) as HomePaired;

    expect(result.copy, isA<HeldCopyUnreadable>());
  });

  /// Put a copy on disk under [scope], through the real store.
  ///
  /// Copied from `snapshot_refresh_test.dart`'s `givenHeldCopy` and widened by
  /// one parameter, which is the parameter these tests are about.
  Future<void> givenHeldCopyUnder(String scope) async {
    final body = jsonDecode(readFixture(knownFixture)) as Map<String, dynamic>;
    body['pairing_id'] = scope;
    await heldCopies.hold(jsonEncode(body));
  }

  group('a rotation mid-load', () {
    // **Two claims, and they need separate tests because one assertion cannot
    // hold both.** Reversing the vault reads leaves the screen's `copy` absent
    // either way in the obvious setup — under the correct order because the new
    // pairing holds nothing, under the reversed one because the attempt filed
    // under the new pairing while the screen looked under the old. And the store
    // keeps one document, so a copy seeded under the other id does not survive a
    // successful fetch. So each claim gets the setup that can falsify it alone.
    test('files the copy under the pairing the attempt started in', () async {
      final store = _RotatingPairingStore(encoded, encodedOtherPairing);

      final result = await loadHome(route: thePairedHost(), store: store) as HomePaired;

      expect(store.reads, 2, reason: 'the vault must be read once per attempt and once per screen');
      expect(await heldCopies.read(pairingId), isA<HeldCopyHeld>());
      // The pairing in force now holds nothing under it, which is the honest
      // answer: the copy that just landed belongs to a relationship the owner
      // has closed, and its payload key went with it.
      expect(result.copy, isA<HeldCopyAbsent>());
    });

    test('draws from the pairing in force when it draws', () async {
      // A host with nothing to serve, so the seeded copy survives the attempt
      // and the screen's own scope becomes the only thing under test.
      await givenHeldCopyUnder(otherPairingId);
      final store = _RotatingPairingStore(encoded, encodedOtherPairing);

      final result = await loadHome(route: answering(HttpStatus.notFound), store: store);

      expect((result as HomePaired).copy, isA<HeldCopyHeld>());
    });

    test('and the control: no rotation shows the copy the attempt just filed', () async {
      final store = _RotatingPairingStore(encoded, encoded);

      final result = await loadHome(route: thePairedHost(), store: store) as HomePaired;

      expect(store.reads, 2);
      expect(result.copy, isA<HeldCopyHeld>());
    });
  });

  test('the fixture this file serves is the one the pairing above can open', () async {
    // Otherwise every case here degrades into a rejection and still passes, for
    // the wrong reason: no copy, a filed failure, and the same assertions green.
    final envelope = jsonDecode(envelopeFixture('paired_envelope.json')) as Map<String, dynamic>;
    expect(envelope['pairing_id'], pairingId);
    expect(otherPairingId, isNot(pairingId));
  });

  test('a load never throws, whatever the pairing layer does', () async {
    // [HomeLoader.load]'s contract, and the reason it has one: a load that
    // escaped as an exception is a screen with nothing on it, while every state
    // above is something the owner can read and act on.
    await expectLater(loadHome(store: FailingPairingStore()), completes);
  });
}
