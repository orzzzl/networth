import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/fetch_diagnostics_store.dart';
import 'package:networth_app/src/data/held_copy_store.dart';
import 'package:networth_app/src/data/seq_baseline_store.dart';
import 'package:networth_app/src/data/snapshot_reader.dart';
import 'package:networth_app/src/data/snapshot_refresh.dart';
import 'package:networth_app/src/data/snapshot_transport.dart';
import 'dart:async';

import 'package:networth_app/src/domain/copy_freshness.dart';
import 'package:networth_app/src/domain/fetch_diagnostics.dart';
import 'package:networth_app/src/domain/held_copy.dart';
import 'package:networth_app/src/domain/publication_seq.dart';
import 'package:networth_app/src/domain/seq_baseline.dart';
import 'package:networth_app/src/pairing/pairing_vault.dart';

import 'fixtures.dart';

/// The `seq` inside `paired_envelope.json`, which is `known.json`'s.
///
/// **Read from the fixture rather than written here**, so that regenerating the
/// envelope with a different counter moves every expectation below with it. A
/// literal would turn a stale fixture into a green suite asserting the wrong
/// comparison, which is the whole subject of these tests.
final PublicationSeq servedSeq =
    PublicationSeq.parse(loadFixture(knownFixture).seq);

PublicationSeq seqOf(int offset) =>
    PublicationSeq.parse('${servedSeq.value + offset}');

/// A held-copy store whose writes fail, which on a device is a full disk.
///
/// Reads are delegated to a real store so a test can still see that the copy on
/// disk is the *old* one rather than merely absent — "the write failed" and
/// "there was never anything there" are the two states this file's rollback
/// argument is about.
class _UnwritableHeldCopyStore implements HeldCopyStore {
  _UnwritableHeldCopyStore(this.inner);

  final HeldCopyStore inner;

  @override
  Future<HeldCopyState> read(String pairingId) => inner.read(pairingId);

  @override
  Future<void> hold(String source) async => throw const FileSystemException('no space left');
}

/// A baseline store whose writes fail, the other half of the same disk.
class _UnwritableBaselineStore implements SeqBaselineStore {
  _UnwritableBaselineStore(this.inner);

  final SeqBaselineStore inner;

  @override
  Future<BaselineState> read(String pairingId) => inner.read(pairingId);

  @override
  Future<void> write(SeqBaseline baseline) async =>
      throw const FileSystemException('no space left');
}

/// Parks the first `hold` until released, and counts them.
///
/// The count is what makes the serialization assertion a presence rather than a
/// timing guess: while the first refresh is parked inside this store, a second
/// that had started would have to reach it, and the count would be two.
class _GatedHeldCopyStore implements HeldCopyStore {
  _GatedHeldCopyStore(this.inner);

  final HeldCopyStore inner;
  final Completer<void> entered = Completer<void>();
  final Completer<void> release = Completer<void>();
  int holds = 0;

  @override
  Future<HeldCopyState> read(String pairingId) => inner.read(pairingId);

  @override
  Future<void> hold(String source) async {
    holds += 1;
    if (holds == 1) {
      entered.complete();
      await release.future;
    }
    await inner.hold(source);
  }
}

void main() {
  late Directory directory;
  late FileFetchDiagnosticsStore diagnostics;
  late FileSeqBaselineStore baselines;
  late FileHeldCopyStore heldCopies;

  /// The instant every attempt below is stamped with. Fixed, because what these
  /// tests assert about it is *which* record carries it, never how long
  /// anything took.
  final at = DateTime.utc(2026, 9, 24, 18);

  setUp(() async {
    directory = await Directory.systemTemp.createTemp('snapshot-refresh-test');
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

  /// One refresh against [route], through the reader the app ships.
  ///
  /// The bytes arrive over a real loopback socket for the reason
  /// `snapshot_reader_test.dart` gives: the outcomes this file maps should be
  /// the outcomes the transport actually produces, not a hand-built set of them
  /// standing where they go.
  Future<SnapshotRefresher> refresher({
    TestRoute? route,
    SecureStringStore? store,
    HeldCopyStore? copies,
    SeqBaselineStore? baselineStore,
  }) async {
    final port = route == null ? 0 : await route.start();
    if (route != null) {
      addTearDown(route.stop);
    }
    return SnapshotRefresher(
      reader: PairedSnapshotReader(
        vault: PairingVault(store: store ?? MemoryPairingStore(encoded)),
        openTransport: (provision) => SnapshotTransport(
          host: InternetAddress.loopbackIPv4.address,
          port: port,
          deadline: const Duration(seconds: 5),
        ),
      ),
      diagnostics: diagnostics,
      baselines: baselineStore ?? baselines,
      heldCopies: copies ?? heldCopies,
      clock: () => at,
    );
  }

  Future<SnapshotRefreshOutcome> refresh({
    TestRoute? route,
    SecureStringStore? store,
    HeldCopyStore? copies,
    SeqBaselineStore? baselineStore,
  }) async =>
      (await refresher(
        route: route,
        store: store,
        copies: copies,
        baselineStore: baselineStore,
      ))
          .refresh();

  TestRoute thePairedHost() => serving(envelopeFixture('paired_envelope.json'));

  Future<FetchDiagnostics> storedDiagnostics() async {
    final state = await diagnostics.read(pairingId);
    expect(state, isA<DiagnosticsHeld>(), reason: 'no diagnostics were written');
    return (state as DiagnosticsHeld).diagnostics;
  }

  Future<SeqBaseline> storedBaseline() async {
    final state = await baselines.read(pairingId);
    expect(state, isA<BaselineHeld>(), reason: 'no baseline was written');
    return (state as BaselineHeld).baseline;
  }

  Future<void> givenBaseline(PublicationSeq lastSeq, {DowngradeWarning? warning}) =>
      baselines.write(
        SeqBaseline(pairingId: pairingId, lastSeq: lastSeq, warning: warning),
      );

  group('the transport fault table', () {
    test('a fault the phone cannot place is never blamed on the host', () {
      // Both values here are the transport refusing to guess, and the class
      // they map to is the one that also refuses. `notThisRoute` is the
      // interesting one: its own doc names a captive portal as the case it
      // exists for, so `transportError` — *"your server answered with something
      // this app couldn't use"* — would re-commit through the copy exactly the
      // misattribution the fault was added to prevent.
      expect(
        classifyTransportFault(SnapshotTransportFault.unclassified),
        FetchFailureClass.unknownFailure,
      );
      expect(
        classifyTransportFault(SnapshotTransportFault.notThisRoute),
        FetchFailureClass.unknownFailure,
      );
    });

    test('a timeout is a host that did not answer, not an unplaceable fault', () {
      // The mapping task `22` turns on. On a tailnet MagicDNS keeps resolving an
      // offline node, so the signature of the lost VPS this app exists to report
      // is a connect that goes out and never comes back. Answering it with
      // *"this device can't say why"* would mute it.
      expect(
        classifyTransportFault(SnapshotTransportFault.timedOut),
        FetchFailureClass.hostUnreachable,
      );
      expect(
        classifyTransportFault(SnapshotTransportFault.connectionRefused),
        FetchFailureClass.hostUnreachable,
      );
      expect(
        classifyTransportFault(SnapshotTransportFault.hostUnreachable),
        FetchFailureClass.hostUnreachable,
      );
    });

    test('only a path that never opened is "no network here"', () {
      expect(
        classifyTransportFault(SnapshotTransportFault.nameNotResolved),
        FetchFailureClass.offline,
      );
      expect(
        classifyTransportFault(SnapshotTransportFault.networkUnreachable),
        FetchFailureClass.offline,
      );
      // And nothing else is, which is the half that stops the table from
      // drifting into the reassuring answer: `offline` says the owner should do
      // nothing, so every fault that reaches it has to have evidence the phone
      // itself is what is disconnected.
      for (final fault in SnapshotTransportFault.values) {
        if (fault == SnapshotTransportFault.nameNotResolved ||
            fault == SnapshotTransportFault.networkUnreachable) {
          continue;
        }
        expect(
          classifyTransportFault(fault),
          isNot(FetchFailureClass.offline),
          reason: '$fault claims the phone has no network',
        );
      }
    });

    test('an exchange that began is the host answering badly', () {
      expect(
        classifyTransportFault(SnapshotTransportFault.connectionLost),
        FetchFailureClass.transportError,
      );
      expect(
        classifyTransportFault(SnapshotTransportFault.responseTooLarge),
        FetchFailureClass.transportError,
      );
    });

    test('no transport fault can ask the owner to re-pair', () {
      // `credentialRejected` renders as *"it needs pairing again"*, and a
      // re-pair rotates the key on a host that may be working. Nothing on the
      // wire can justify it: this route sends no credential, so no socket error
      // and no status code is evidence about the pairing.
      for (final fault in SnapshotTransportFault.values) {
        expect(
          classifyTransportFault(fault),
          isNot(FetchFailureClass.credentialRejected),
          reason: '$fault would tell the owner to re-pair',
        );
      }
    });

    test('an envelope that would not open is the only re-pair evidence', () {
      expect(
        classifyRejection(SnapshotRejection.notAuthentic),
        FetchFailureClass.credentialRejected,
      );
      expect(
        classifyRejection(SnapshotRejection.otherPairing),
        FetchFailureClass.credentialRejected,
      );
      expect(
        classifyRejection(SnapshotRejection.notAnEnvelope),
        FetchFailureClass.transportError,
      );
      expect(
        classifyRejection(SnapshotRejection.headerDisagreement),
        FetchFailureClass.transportError,
      );
      expect(
        classifyRejection(SnapshotRejection.payloadNotReadable),
        FetchFailureClass.transportError,
      );
    });
  });

  group('an attempt with no pairing to file it under', () {
    test('an unpaired phone records nothing at all', () async {
      final outcome = await refresh(store: MemoryPairingStore());

      expect(outcome, isA<RefreshNotAttempted>());
      expect((outcome as RefreshNotAttempted).reason, NoPairing.notPaired);
      // Not "wrote an empty record": every record is scoped to a `pairing_id`,
      // and there is none to scope one to. A file appearing here would mean the
      // refresher had invented a scope.
      expect(directory.listSync(), isEmpty);
    });

    test('protected storage that throws also records nothing', () async {
      final outcome = await refresh(store: FailingPairingStore());

      expect(outcome, isA<RefreshNotAttempted>());
      expect((outcome as RefreshNotAttempted).reason, NoPairing.pairingUnreadable);
      expect(directory.listSync(), isEmpty);
    });
  });

  group('an attempt that brought back no payload', () {
    test('a 404 is recorded as reaching a host with nothing, not as a failure', () async {
      final outcome = await refresh(route: answering(HttpStatus.notFound));

      expect((outcome as RefreshKeptHeldCopy).refusal, RefreshRefusal.hostHasNoPublication);
      final record = await storedDiagnostics();
      // The distinction the whole record exists for: as a failure this would
      // drop §9.1's reason to `CANNOT_CHECK` and report a dead publisher as a
      // network problem.
      expect(record.foundNoPublication, isTrue);
      expect(record.lastError, isNull);
      expect(record.lastAttemptAt, at);
    });

    test('a 503 is the host answering badly', () async {
      final outcome = await refresh(route: answering(HttpStatus.serviceUnavailable));

      expect((outcome as RefreshKeptHeldCopy).refusal, RefreshRefusal.attemptFailed);
      final record = await storedDiagnostics();
      expect(record.lastError, FetchFailureClass.transportError);
      expect(record.foundNoPublication, isFalse);
    });

    test('a status this route does not define is not a rejected credential', () async {
      final outcome = await refresh(route: answering(HttpStatus.unauthorized));

      expect((outcome as RefreshKeptHeldCopy).refusal, RefreshRefusal.attemptFailed);
      // A `401` from something on the tailnet is a status `serve.py` never
      // sends, not a refusal of a credential this route never carries. Reading
      // it as one would tell the owner to re-pair a working host.
      expect((await storedDiagnostics()).lastError, FetchFailureClass.transportError);
    });

    test('a failed attempt keeps the last success it followed', () async {
      await diagnostics.write(
        FetchDiagnostics.succeeded(
          pairingId: pairingId,
          at: at.subtract(const Duration(hours: 9)),
          seq: servedSeq,
        ),
      );

      await refresh(route: answering(HttpStatus.serviceUnavailable));

      final record = await storedDiagnostics();
      expect(record.lastSuccess?.at, at.subtract(const Duration(hours: 9)));
      expect(record.lastSuccess?.seq, servedSeq);
      expect(record.lastAttemptAt, at);
    });

    test('a damaged record is not overwritten with "never succeeded"', () async {
      await File('${directory.path}/${FileFetchDiagnosticsStore.fileName}')
          .writeAsString('{"pairing_id": "$pairingId", "last_fetch_attempt_at": 7}');

      await refresh(route: answering(HttpStatus.serviceUnavailable));

      // `after: null` is the claim *"this phone has attempted under this pairing
      // and never succeeded"* — `CannotCheck.since` renders exactly that — and
      // the record that would carry it was unreadable, so nothing observed it.
      // Writing it is the `NeverFetched`-instead-of-`RecordsUnusable` mistake one
      // field down; leaving the damage costs this attempt's timestamp and says
      // something true.
      expect(await diagnostics.read(pairingId), isA<DiagnosticsUnreadable>());
    });

    test('a success heals a damaged record, because it carries nothing forward', () async {
      await File('${directory.path}/${FileFetchDiagnosticsStore.fileName}')
          .writeAsString('{"pairing_id": "$pairingId", "last_fetch_attempt_at": 7}');

      await refresh(route: thePairedHost());

      final record = await storedDiagnostics();
      expect(record.lastSuccess?.seq, servedSeq);
      expect(record.lastAttemptAt, at);
    });
  });

  group('an envelope the phone refused', () {
    test('a tag that did not verify asks for a re-pair and warns about nothing', () async {
      final outcome = await refresh(
        route: thePairedHost(),
        store: MemoryPairingStore(encodedWithOtherKey),
      );

      expect((outcome as RefreshKeptHeldCopy).refusal, RefreshRefusal.attemptFailed);
      expect((await storedDiagnostics()).lastError, FetchFailureClass.credentialRejected);
      // §9.3's warning is for the two rows in its table. A bad tag is neither,
      // and raising the persistent downgrade warning for it would leave the
      // owner an alarm that clears only when a greater `seq` arrives.
      expect(await baselines.read(pairingId), isA<BaselineAbsent>());
    });

    test('an envelope for another pairing raises §9.3 row four', () async {
      await givenBaseline(servedSeq);

      // `known_envelope.json` names `fixture-pairing`; this phone is paired to
      // something else, which is *"the phone is talking to something that is not
      // its daemon"*.
      final outcome = await refresh(route: serving(envelopeFixture('known_envelope.json')));

      expect((outcome as RefreshKeptHeldCopy).refusal, RefreshRefusal.foreignPairing);
      final baseline = await storedBaseline();
      expect(baseline.warning?.cause, DowngradeCause.foreignPairing);
      // The foreign counter is not kept beside this one: they are unrelated
      // numbers and nothing true can be said about their order.
      expect(baseline.warning?.refusedSeq, isNull);
      expect(baseline.lastSeq, servedSeq);
      expect((await storedDiagnostics()).lastError, FetchFailureClass.credentialRejected);
    });

    test('a foreign pairing on a phone with no baseline warns about nothing', () async {
      final outcome = await refresh(route: serving(envelopeFixture('known_envelope.json')));

      expect((outcome as RefreshKeptHeldCopy).refusal, RefreshRefusal.foreignPairing);
      // There is no `last_seq` to attach the warning to and no copy for it to
      // defend. A placeholder baseline would be a number this phone never held.
      expect(await baselines.read(pairingId), isA<BaselineAbsent>());
    });
  });

  group('I6, on a payload that arrived', () {
    test('a phone with no baseline accepts its first payload on trust', () async {
      final outcome = await refresh(route: thePairedHost());

      expect(outcome, isA<RefreshAccepted>());
      expect((await storedBaseline()).lastSeq, servedSeq);
      expect(await heldCopies.read(pairingId), isA<HeldCopyHeld>());
      final record = await storedDiagnostics();
      expect(record.lastSuccess?.seq, servedSeq);
      expect(record.lastError, isNull);
      expect(record.foundNoPublication, isFalse);
    });

    test('a greater seq is accepted and clears the warning', () async {
      await givenBaseline(
        seqOf(-1),
        warning: DowngradeWarning(
          cause: DowngradeCause.olderPublication,
          at: at.subtract(const Duration(days: 1)),
          refusedSeq: seqOf(-4),
        ),
      );

      final outcome = await refresh(route: thePairedHost());

      expect(outcome, isA<RefreshAccepted>());
      final baseline = await storedBaseline();
      expect(baseline.lastSeq, servedSeq);
      // §9.3 point 1: the warning clears when a greater `seq` *actually
      // arrives*, and this is that event.
      expect(baseline.warning, isNull);
    });

    test('the same seq is "nothing new" and does not paper over a warning', () async {
      final warning = DowngradeWarning(
        cause: DowngradeCause.olderPublication,
        at: at.subtract(const Duration(days: 1)),
        refusedSeq: seqOf(-4),
      );
      await givenBaseline(servedSeq, warning: warning);

      final outcome = await refresh(route: thePairedHost());

      expect(outcome, isA<RefreshAccepted>());
      final baseline = await storedBaseline();
      expect(baseline.lastSeq, servedSeq);
      // *"not on the next successful fetch, which would let a single good
      // response paper over an unexplained downgrade."* This fetch brought
      // nothing new, so it is not the event that clears it.
      expect(baseline.warning, warning);
    });

    test('the same seq still re-holds the copy, so a damaged one recovers', () async {
      await givenBaseline(servedSeq);
      await File('${directory.path}/${FileHeldCopyStore.fileName}').writeAsString('{');
      expect(await heldCopies.read(pairingId), isA<HeldCopyUnreadable>());

      await refresh(route: thePairedHost());

      // A baseline at `seq` with a damaged copy would otherwise show nothing
      // until the host published again — which, on the daemon that has stopped,
      // is never. Re-holding the publication the baseline already names cannot
      // downgrade anything.
      expect(await heldCopies.read(pairingId), isA<HeldCopyHeld>());
    });

    test('a lower seq is refused, and the phone keeps what it holds', () async {
      // The copy comes from a real accepted refresh rather than from bytes
      // pasted here, so "what it holds" is what the shipped path stores.
      await refresh(route: thePairedHost());
      // The phone has since held something newer than the host now serves,
      // which is §9.3's restore/rollback row.
      await givenBaseline(seqOf(1));

      final outcome = await refresh(route: thePairedHost());

      expect((outcome as RefreshKeptHeldCopy).refusal, RefreshRefusal.downgradeRefused);
      expect(await heldCopies.read(pairingId), isA<HeldCopyHeld>());
      final baseline = await storedBaseline();
      // The baseline does not move: the phone keeps the newer copy it holds.
      expect(baseline.lastSeq, seqOf(1));
      expect(baseline.warning?.cause, DowngradeCause.olderPublication);
      expect(baseline.warning?.refusedSeq, servedSeq);
      expect(baseline.warning?.at, at);
    });

    test('a refused payload is still a successful fetch on the record', () async {
      await givenBaseline(seqOf(1));

      await refresh(route: thePairedHost());

      final record = await storedDiagnostics();
      // §9.1's `last_fetch_seq` is *the `seq` the last successful fetch
      // returned*, not the one the phone kept. Recording the refusal as a failed
      // fetch would erase the divergence conjunct 3 reads — the phone would
      // report a network problem for a payload it received and rejected.
      expect(record.lastError, isNull);
      expect(record.lastSuccess?.seq, servedSeq);
      expect(record.lastSuccess?.seq, isNot((await storedBaseline()).lastSeq));
    });

    test('a baseline that will not parse is never accept-on-trust', () async {
      await File('${directory.path}/${FileSeqBaselineStore.fileName}')
          .writeAsString('{"pairing_id": "$pairingId", "last_seq": 41}');

      final outcome = await refresh(route: thePairedHost());

      expect((outcome as RefreshKeptHeldCopy).refusal, RefreshRefusal.baselineUnreadable);
      // Damaging one file must not be the way to disable I6: §9.3's
      // accept-on-trust case is a phone with *no* baseline, and this is not that
      // phone. The damaged bytes stand rather than being replaced by a fresh
      // baseline the payload would have set.
      expect(await baselines.read(pairingId), isA<BaselineUnreadable>());
      expect(await heldCopies.read(pairingId), isA<HeldCopyAbsent>());
      // And the fetch is still recorded as the success it was.
      expect((await storedDiagnostics()).lastSuccess?.seq, servedSeq);
    });
  });

  group('a copy the phone could not keep', () {
    test('nothing is advanced, because nothing is written before the copy', () async {
      await givenBaseline(seqOf(-1));

      final outcome = await refresh(
        route: thePairedHost(),
        copies: _UnwritableHeldCopyStore(heldCopies),
      );

      expect((outcome as RefreshKeptHeldCopy).refusal, RefreshRefusal.notStored);
      // The order is the whole of it. With the baseline written first there was
      // a window in which it named a `seq` no copy on disk carried, and the
      // rollback that closed it could not run at all if the process was killed
      // instead of the write failing.
      expect((await storedBaseline()).lastSeq, seqOf(-1));
      expect((await storedDiagnostics()).lastSuccess?.seq, servedSeq);
    });

    test('the copy on screen is never reported as confirmed by the host', () async {
      // Codex's review probe on PR #119, adopted as a regression. The defect it
      // found: with the baseline advanced and the copy write failed, conjunct 3
      // compared 41 against 41 and §9.1 said `HOST_NOT_PUBLISHING` — *"nothing
      // has been published since"* — over a copy the host had in fact just
      // published past.
      await refresh(route: thePairedHost());
      final held = (await heldCopies.read(pairingId) as HeldCopyHeld).payload;
      await givenBaseline(seqOf(-1));
      await diagnostics.write(
        FetchDiagnostics.succeeded(pairingId: pairingId, at: at, seq: servedSeq),
      );

      final state = evaluateCopyState(
        publishedAt: held.publishedAt,
        publishInterval: held.publishInterval,
        grace: held.grace,
        deviceNow: at.add(const Duration(days: 30)),
        diagnostics: await diagnostics.read(pairingId),
        baseline: await baselines.read(pairingId),
        continuity: trustedClock,
      ) as CopyStale;

      expect(state.reason, isA<CannotCheck>());
      expect((state.reason as CannotCheck).cause, isA<ServedPayloadNotHeld>());
    });

    test('a first payload that cannot be kept leaves no baseline at all', () async {
      final outcome = await refresh(
        route: thePairedHost(),
        copies: _UnwritableHeldCopyStore(heldCopies),
      );

      expect((outcome as RefreshKeptHeldCopy).refusal, RefreshRefusal.notStored);
      expect(await baselines.read(pairingId), isA<BaselineAbsent>());
      expect(await heldCopies.read(pairingId), isA<HeldCopyAbsent>());
    });

    test('a baseline behind the copy is repaired, and does not clear a warning', () async {
      // **The replay floor is the higher of the two records**, and this is where
      // that is observable. The fixture serves one `seq`, so the refusal
      // direction cannot be driven directly; the warning can. With the floor
      // read from the baseline alone, `servedSeq > seqOf(-1)` would make this a
      // newer publication and clear the warning — papering over an unexplained
      // downgrade with a fetch that brought nothing new, which is the one thing
      // §9.3 point 1 forbids.
      await refresh(route: thePairedHost());
      final warning = DowngradeWarning(
        cause: DowngradeCause.olderPublication,
        at: at.subtract(const Duration(days: 1)),
        refusedSeq: seqOf(-4),
      );
      await givenBaseline(seqOf(-1), warning: warning);

      expect(await refresh(route: thePairedHost()), isA<RefreshAccepted>());

      final baseline = await storedBaseline();
      expect(baseline.lastSeq, servedSeq, reason: 'the lagging baseline is repaired');
      expect(baseline.warning, warning, reason: 'nothing newer than the copy arrived');
    });

    test('a baseline that cannot be written still accepts the copy on disk', () async {
      final outcome = await refresh(
        route: thePairedHost(),
        baselineStore: _UnwritableBaselineStore(baselines),
      );

      // The copy is on disk, so the payload was accepted and the screen shows
      // it. The floor does not lag with the baseline, because it is read from
      // the copy — reporting a refusal here would say the phone kept the old
      // copy while it is holding the new one.
      expect(outcome, isA<RefreshAccepted>());
      expect(await heldCopies.read(pairingId), isA<HeldCopyHeld>());
      expect(await baselines.read(pairingId), isA<BaselineAbsent>());
    });
  });

  group('two refreshes at once', () {
    test('the second does not start until the first has finished', () async {
      // Review's finding, and the reason it is a class rather than an instance:
      // Dart has one isolate, so this is not thread-safety — every `await` in a
      // refresh is a point where a second one can run to completion in between,
      // read a baseline the first has already advanced, and act on it. The
      // reproduction that found it had the first refresh roll back over a copy
      // the second had accepted, leaving the replay floor below it.
      await givenBaseline(seqOf(-1));
      final gate = _GatedHeldCopyStore(heldCopies);
      // **One refresher, deliberately.** The serialization is a property of the
      // instance that owns the three stores, and two instances over one
      // directory are outside its stated contract — a test that used two would
      // be asserting something this class does not claim.
      final one = await refresher(route: thePairedHost(), copies: gate);

      final first = one.refresh();
      await gate.entered.future;
      final second = one.refresh();
      // The gate is what proves serialization rather than a timing guess: the
      // second refresh cannot have reached the copy store while the first is
      // parked inside it.
      await pumpEventQueue();
      expect(gate.holds, 1);

      gate.release.complete();
      expect(await first, isA<RefreshAccepted>());
      expect(await second, isA<RefreshAccepted>());
      expect(gate.holds, 2);
      expect((await storedBaseline()).lastSeq, servedSeq);
      expect(await heldCopies.read(pairingId), isA<HeldCopyHeld>());
    });
  });
}
