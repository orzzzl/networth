import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/fetch_diagnostics_store.dart';
import 'package:networth_app/src/domain/fetch_diagnostics.dart';
import 'package:networth_app/src/domain/payload_format_exception.dart';
import 'package:networth_app/src/domain/publication_seq.dart';

/// Stands in for the exception types this path can raise that `dart:io` does not
/// define — `path_provider`'s `MissingPlatformDirectoryException` is the real
/// one. Declared here rather than depended on so the test exercises the *shape*
/// of the problem without pinning one package's class name.
class _NotAFileSystemException implements Exception {
  const _NotAFileSystemException();
}

void main() {
  late Directory directory;
  late File file;
  late FileFetchDiagnosticsStore store;

  setUp(() async {
    directory = await Directory.systemTemp.createTemp('fetch-diagnostics-test');
    file = File('${directory.path}/${FileFetchDiagnosticsStore.fileName}');
    store = FileFetchDiagnosticsStore(open: () async => file);
  });

  tearDown(() async => directory.delete(recursive: true));

  DateTime utc(int day, int hour) => DateTime.utc(2026, 9, day, hour);

  FetchDiagnostics succeeded({
    String pairingId = 'pairing-a',
    DateTime? at,
    String seq = '7',
  }) =>
      FetchDiagnostics.succeeded(
        pairingId: pairingId,
        at: at ?? utc(20, 9),
        seq: PublicationSeq.parse(seq),
      );

  FetchDiagnostics failed({
    String pairingId = 'pairing-a',
    DateTime? at,
    FetchFailureClass error = FetchFailureClass.offline,
    FetchSuccess? after,
  }) =>
      FetchDiagnostics.failed(
        pairingId: pairingId,
        at: at ?? utc(21, 9),
        error: error,
        after: after,
      );

  FetchDiagnostics reachedHostWithNothing({
    String pairingId = 'pairing-a',
    DateTime? at,
    FetchSuccess? after,
  }) =>
      FetchDiagnostics.foundNoPublication(
        pairingId: pairingId,
        at: at ?? utc(21, 9),
        after: after,
      );

  FetchDiagnostics held(DiagnosticsState state) {
    expect(state, isA<DiagnosticsHeld>());
    return (state as DiagnosticsHeld).diagnostics;
  }

  group('a pairing with no diagnostics', () {
    test('reads as absent when nothing has ever been written', () async {
      expect(await store.read('pairing-a'), isA<DiagnosticsAbsent>());
    });

    test('reads as absent when the stored record belongs to another pairing', () async {
      // The third conjunct of `HOST_NOT_PUBLISHING` compares `last_fetch_seq`
      // with the per-pairing `last_seq`. A reading taken under another pairing is
      // a reading of a different counter, and two unrelated counters are free to
      // coincide — which would accuse a host that is publishing fine.
      await store.write(succeeded(pairingId: 'pairing-a', seq: '400'));

      expect(await store.read('pairing-b'), isA<DiagnosticsAbsent>());
      expect(await store.read('pairing-a'), isA<DiagnosticsHeld>());
    });
  });

  group('a successful attempt', () {
    test('stores §9.1\'s field names, in UTC, with the host\'s own seq bytes', () async {
      // '10' is the value a lexicographic comparison gets wrong, and it is also
      // the one a sloppy round-trip would be free to rewrite.
      await store.write(succeeded(at: utc(20, 9), seq: '10'));

      final record = held(await store.read('pairing-a'));
      expect(record.lastAttemptAt, utc(20, 9));
      expect(record.lastError, isNull);
      expect(record.lastSuccess?.at, utc(20, 9));
      expect(record.lastSuccess?.seq.wire, '10');
      expect(record.lastSuccess?.seq.value, 10);
      expect(jsonDecode(await file.readAsString()), <String, Object?>{
        'pairing_id': 'pairing-a',
        'last_fetch_attempt_at': '2026-09-20T09:00:00.000Z',
        'last_fetch_success_at': '2026-09-20T09:00:00.000Z',
        'last_fetch_seq': '10',
      });
    });

    test('is its own last success, so the two instants cannot be given separately', () async {
      // The constructor takes one instant. Conjunct 2 reads
      // `last_fetch_attempt_at == last_fetch_success_at` as "nothing has failed
      // since", and a shape that let a caller pass two instants would let that
      // conjunct be false on a record whose last attempt succeeded.
      final record = succeeded(at: utc(20, 9));

      expect(record.lastAttemptAt.isAtSameMomentAs(record.lastSuccess!.at), isTrue);
    });

    test('writes the instant in UTC even when it is given in local time', () async {
      // `AGENTS.md`: every stored timestamp is UTC with an explicit zone, and
      // staleness math is done in UTC.
      final local = DateTime(2026, 9, 20, 9);
      await store.write(succeeded(at: local));

      final stored = jsonDecode(await file.readAsString()) as Map<String, Object?>;
      expect(stored['last_fetch_attempt_at'], endsWith('Z'));
      expect(held(await store.read('pairing-a')).lastAttemptAt, local.toUtc());
    });
  });

  group('a failed attempt', () {
    test('keeps the error class beside the success it did not replace', () async {
      await store.write(
        failed(
          at: utc(21, 9),
          error: FetchFailureClass.hostUnreachable,
          after: FetchSuccess(at: utc(20, 9), seq: PublicationSeq.parse('7')),
        ),
      );

      final record = held(await store.read('pairing-a'));
      expect(record.lastAttemptAt, utc(21, 9));
      expect(record.lastError, FetchFailureClass.hostUnreachable);
      expect(record.lastSuccess?.at, utc(20, 9));
      expect(record.lastSuccess?.seq.wire, '7');
    });

    test('distinguishes never having succeeded from never having attempted', () async {
      // §9.1's "never fetched" is [DiagnosticsAbsent]. A phone that has tried and
      // failed every time is a different state with a different error class to
      // show, and collapsing them would report "never fetched" to a phone that
      // has been trying all week.
      await store.write(failed(error: FetchFailureClass.offline));

      final record = held(await store.read('pairing-a'));
      expect(record.lastSuccess, isNull);
      expect(record.lastError, FetchFailureClass.offline);
    });

    test('round-trips every error class through its stored spelling', () async {
      for (final error in FetchFailureClass.values) {
        await store.write(failed(error: error));

        expect(held(await store.read('pairing-a')).lastError, error);
      }
    });

    test('and each class is stored as exactly these bytes, which are a format', () async {
      // **The round trip above cannot see this and mutation testing proved it:**
      // changing a stored spelling was the must-fail control, and every test
      // stayed green, because write and read consult the same map and a
      // self-consistent rename round-trips perfectly. What it breaks is on the
      // *other* side of an app update — a phone upgrading to a build that spells
      // one of these differently reads its own record as damaged and shows the
      // owner a corrupted-diagnostics message for a file that was never
      // corrupt. So the literals are asserted here, where changing one is a
      // deliberate edit to a test that says it is the format.
      const spellings = <FetchFailureClass, String>{
        FetchFailureClass.offline: 'OFFLINE',
        FetchFailureClass.hostUnreachable: 'HOST_UNREACHABLE',
        FetchFailureClass.credentialRejected: 'CREDENTIAL_REJECTED',
        FetchFailureClass.transportError: 'TRANSPORT_ERROR',
        FetchFailureClass.unknownFailure: 'UNKNOWN_FAILURE',
      };
      expect(
        spellings.keys,
        unorderedEquals(FetchFailureClass.values),
        reason: 'a class added without a stored spelling has no format at all',
      );

      for (final entry in spellings.entries) {
        await store.write(failed(error: entry.key));

        final stored = jsonDecode(await file.readAsString()) as Map<String, Object?>;
        expect(stored['last_fetch_error'], entry.value);
      }
    });

    test('survives a stamp at or before the success it follows', () async {
      // Attempt order is not wall-clock order. This store used to refuse such a
      // record, which kept the older *success* on disk — so conjunct 2 saw no
      // held error and the phone reported `HOST_NOT_PUBLISHING` about a host it
      // had just failed to reach. Refusing discarded the newer, worse news.
      final success = FetchSuccess(at: utc(20, 9), seq: PublicationSeq.parse('7'));

      for (final at in <DateTime>[utc(19, 9), utc(20, 9), utc(20, 10)]) {
        await store.write(failed(at: at, after: success));

        final stored = held(await store.read('pairing-a'));
        expect(stored.lastError, FetchFailureClass.offline);
        expect(stored.lastAttemptAt.isAtSameMomentAs(at), isTrue);
        expect(stored.lastSuccess, success, reason: 'the success it followed is kept');
      }
    });

    test('and an out-of-order stamp is this record\'s own clock evidence', () {
      // Two readings of this device's own clock disagreeing. It survives a
      // restart, unlike any claim about "now" — but it does not bound how far
      // back the clock went, and it cannot see a correction applied while no
      // attempt was made at all.
      final success = FetchSuccess(at: utc(20, 9), seq: PublicationSeq.parse('7'));

      expect(failed(at: utc(19, 9), after: success).clockMovedBackwards, isTrue);
      expect(failed(at: utc(20, 9), after: success).clockMovedBackwards, isTrue);
      expect(failed(at: utc(20, 10), after: success).clockMovedBackwards, isFalse);
      expect(failed(at: utc(19, 9)).clockMovedBackwards, isFalse,
          reason: 'no success to disagree with');
      expect(succeeded(at: utc(20, 9), seq: '7').clockMovedBackwards, isFalse,
          reason: 'the success path makes the two instants one value');
    });
  });

  group('an attempt that reached a host with nothing to serve', () {
    FetchDiagnostics noPublication({DateTime? at, FetchSuccess? after}) =>
        FetchDiagnostics.foundNoPublication(
          pairingId: 'pairing-a',
          at: at ?? utc(21, 9),
          after: after,
        );

    test('is neither a failure nor a success, across the disk', () async {
      final success = FetchSuccess(at: utc(20, 9), seq: PublicationSeq.parse('7'));

      await store.write(noPublication(after: success));

      final record = held(await store.read('pairing-a'));
      expect(record.foundNoPublication, isTrue);
      // Not a failure: reading it as one is what drops §9.1's reason to
      // `CANNOT_CHECK` and reports a dead publisher as a network problem.
      expect(record.lastError, isNull);
      // Not a success either: the attempt that returned a payload is still the
      // older one, and its `seq` is the phone's evidence about what it holds.
      expect(record.lastAttemptAt, utc(21, 9));
      expect(record.lastSuccess?.at, utc(20, 9));
      expect(record.lastSuccess?.seq.wire, '7');
    });

    test('and its key on disk is exactly this, which is a format', () async {
      // Asserted as a literal for the reason the error spellings above are: the
      // round trip cannot see a rename, because write and read consult the same
      // constant. What a rename breaks is the other side of an app update — a
      // phone whose record was written by the previous build reads the key it
      // no longer knows, falls through to the success branch, and finds an
      // attempt that neither succeeded nor failed. Its own record then reads as
      // damaged, and `RecordsUnusable` is what the owner is told about a `404`.
      await store.write(noPublication());

      expect(jsonDecode(await file.readAsString()), <String, Object?>{
        'pairing_id': 'pairing-a',
        'last_fetch_attempt_at': '2026-09-21T09:00:00.000Z',
        'last_fetch_found_no_publication': true,
      });
      expect(FileFetchDiagnosticsStore.noPublicationKey, 'last_fetch_found_no_publication');
    });

    test('and the key is absent, not false, on every other record', () async {
      // So a file written before this key existed reads back as what it was.
      // Writing `false` would also work today and would make the absence
      // ambiguous the moment anything reads the key directly.
      await store.write(succeeded());
      expect(
        (jsonDecode(await file.readAsString()) as Map<String, Object?>).containsKey(
          FileFetchDiagnosticsStore.noPublicationKey,
        ),
        isFalse,
      );

      await store.write(failed());
      expect(
        (jsonDecode(await file.readAsString()) as Map<String, Object?>).containsKey(
          FileFetchDiagnosticsStore.noPublicationKey,
        ),
        isFalse,
      );
    });

    test('and an out-of-order stamp is this record\'s clock evidence too', () {
      // The same argument as the failed-attempt case: a `404` stamped at or
      // before the success it followed came from a clock corrected backwards.
      // Written as `lastError != null`, `clockMovedBackwards` would have gone
      // silent for this whole state.
      final success = FetchSuccess(at: utc(20, 9), seq: PublicationSeq.parse('7'));

      expect(noPublication(at: utc(19, 9), after: success).clockMovedBackwards, isTrue);
      expect(noPublication(at: utc(20, 9), after: success).clockMovedBackwards, isTrue);
      expect(noPublication(at: utc(21, 9), after: success).clockMovedBackwards, isFalse);
    });

    test('and a record claiming both is refused rather than half-believed', () async {
      // The two halves are opposite advice — "your network is down" against
      // "your server has nothing" — so picking one is picking what to tell the
      // owner on no evidence.
      await file.writeAsString(jsonEncode(<String, Object?>{
        'pairing_id': 'pairing-a',
        'last_fetch_attempt_at': '2026-09-21T09:00:00.000Z',
        'last_fetch_error': 'OFFLINE',
        'last_fetch_found_no_publication': true,
      }));

      expect(await store.read('pairing-a'), isA<DiagnosticsUnreadable>());
    });

    test('and a key this store never writes is damage, including false', () async {
      // **The record around it is otherwise valid, and that is the whole test.**
      // Written without the success below, every one of these values reaches
      // `DiagnosticsUnreadable` through a different door — "an attempt that
      // neither succeeded nor failed" — so the test passes whether or not the
      // value is checked at all. Mutation found exactly that: deleting this
      // guard left the first version of this test green. With the success
      // present, an unguarded read returns a held *success*, which is a record
      // this build never wrote being reported as one it did.
      for (final value in <Object?>[false, 'true', 1, 0]) {
        await file.writeAsString(jsonEncode(<String, Object?>{
          'pairing_id': 'pairing-a',
          'last_fetch_attempt_at': '2026-09-21T09:00:00.000Z',
          'last_fetch_success_at': '2026-09-21T09:00:00.000Z',
          'last_fetch_seq': '7',
          'last_fetch_found_no_publication': value,
        }));

        expect(
          await store.read('pairing-a'),
          isA<DiagnosticsUnreadable>(),
          reason: 'a $value here was written by nothing in this build',
        );
      }
    });

    test('and the store will not write a shape its own reader refuses', () async {
      // `write` parses the bytes it is about to commit, so the strictness above
      // is not a rule the writer could quietly diverge from: a `_json` that
      // started emitting the key unconditionally would fail here rather than on
      // the next launch of a phone that had already stored one.
      await store.write(succeeded());
      await store.write(noPublication());

      final stored = jsonDecode(await file.readAsString()) as Map<String, Object?>;
      expect(stored[FileFetchDiagnosticsStore.noPublicationKey], isTrue);
    });
  });

  group('the record is replaced, never appended to', () {
    test('so only the current attempt is kept', () async {
      await store.write(succeeded(at: utc(20, 9), seq: '7'));
      await store.write(
        failed(
          at: utc(21, 9),
          after: FetchSuccess(at: utc(20, 9), seq: PublicationSeq.parse('7')),
        ),
      );

      expect(held(await store.read('pairing-a')).lastError, FetchFailureClass.offline);
      expect(
        directory.listSync().map((entry) => entry.uri.pathSegments.last),
        <String>[FileFetchDiagnosticsStore.fileName],
      );
    });

    test('and a later success clears the error it followed', () async {
      await store.write(failed(at: utc(21, 9), error: FetchFailureClass.credentialRejected));
      await store.write(succeeded(at: utc(22, 9), seq: '8'));

      expect(held(await store.read('pairing-a')).lastError, isNull);
    });
  });

  group('a record that cannot be read is never read as absent', () {
    Future<void> damaged(String bytes) async {
      await file.writeAsString(bytes);
      expect(await store.read('pairing-a'), isA<DiagnosticsUnreadable>());
    }

    test('when it is not JSON', () => damaged('{'));

    test('when it is not a JSON object', () => damaged('[]'));

    test('when it has no pairing_id', () => damaged('{"last_fetch_attempt_at": "2026-09-20T09:00:00Z"}'));

    test('when its pairing_id is not a string', () async {
      // It cannot be scoped away either: "which pairing is this for" is exactly
      // the question it failed to answer.
      await file.writeAsString('{"pairing_id": 1, "last_fetch_attempt_at": "2026-09-20T09:00:00Z"}');

      expect(await store.read('pairing-b'), isA<DiagnosticsUnreadable>());
    });

    test('when it records no attempt', () => damaged('{"pairing_id": "pairing-a"}'));

    test('when the path holds something that is not a readable file', () async {
      // Nothing here can be bypassed by damage — no facts means no conjunct,
      // and the predicate falls to `CANNOT_CHECK`, which blames nobody. But
      // "this phone has never fetched" and "this phone cannot read what it
      // recorded" are different things to tell the owner, and every shape below
      // answers `exists() == false` while being neither absence nor a file.
      Directory(file.path).createSync();
      expect(await store.read('pairing-a'), isA<DiagnosticsUnreadable>(),
          reason: 'a directory standing at the path');
      Directory(file.path).deleteSync();

      Link(file.path).createSync('${directory.path}/no-such-target');
      expect(await store.read('pairing-a'), isA<DiagnosticsUnreadable>(),
          reason: 'a dangling symlink, which fails with a missing file\'s own errno');
      Link(file.path).deleteSync();

      expect(await store.read('pairing-a'), isA<DiagnosticsAbsent>(),
          reason: 'and with the path genuinely clear again, absence is still absence');
    });

    test('when an instant carries no timezone', () async {
      // The case this check exists for: `DateTime.parse` would read it as local
      // time, landing seven or eight hours off on this owner's phone, and
      // conjunct 1 is a comparison that flips inside that margin.
      await damaged(jsonEncode(<String, Object?>{
        'pairing_id': 'pairing-a',
        'last_fetch_attempt_at': '2026-09-20T09:00:00',
        'last_fetch_success_at': '2026-09-20T09:00:00',
        'last_fetch_seq': '7',
      }));
    });

    test('when an instant is not ISO-8601', () async {
      await damaged(jsonEncode(<String, Object?>{
        'pairing_id': 'pairing-a',
        'last_fetch_attempt_at': 'yesterday',
      }));
    });

    test('when it holds only half of a successful fetch', () async {
      await damaged(jsonEncode(<String, Object?>{
        'pairing_id': 'pairing-a',
        'last_fetch_attempt_at': '2026-09-20T09:00:00Z',
        'last_fetch_success_at': '2026-09-20T09:00:00Z',
      }));
      await damaged(jsonEncode(<String, Object?>{
        'pairing_id': 'pairing-a',
        'last_fetch_attempt_at': '2026-09-20T09:00:00Z',
        'last_fetch_seq': '7',
        'last_fetch_error': 'OFFLINE',
      }));
    });

    test('when the attempt neither succeeded nor failed', () async {
      // No error, and no success at the attempt's instant. Nothing true can be
      // read out of it, and either guess feeds the predicate a fact nobody
      // observed.
      await damaged(jsonEncode(<String, Object?>{
        'pairing_id': 'pairing-a',
        'last_fetch_attempt_at': '2026-09-21T09:00:00Z',
      }));
      await damaged(jsonEncode(<String, Object?>{
        'pairing_id': 'pairing-a',
        'last_fetch_attempt_at': '2026-09-21T09:00:00Z',
        'last_fetch_success_at': '2026-09-20T09:00:00Z',
        'last_fetch_seq': '7',
      }));
    });

    test('when the error class is one this build does not know', () async {
      // A newer build's spelling is not a class this one may guess at: every
      // class maps to a different thing for the owner to do.
      await damaged(jsonEncode(<String, Object?>{
        'pairing_id': 'pairing-a',
        'last_fetch_attempt_at': '2026-09-21T09:00:00Z',
        'last_fetch_error': 'DNS_POISONED',
      }));
    });

    test('when the seq is not one the host could have rendered', () async {
      await damaged(jsonEncode(<String, Object?>{
        'pairing_id': 'pairing-a',
        'last_fetch_attempt_at': '2026-09-20T09:00:00Z',
        'last_fetch_success_at': '2026-09-20T09:00:00Z',
        'last_fetch_seq': '007',
      }));
    });

    test('when the file cannot be opened at all', () async {
      // Deterministic everywhere — it needs no permission the runner might have.
      final throwing = FileFetchDiagnosticsStore(
        open: () async => throw const FileSystemException('no documents directory'),
      );

      expect(await throwing.read('pairing-a'), isA<DiagnosticsUnreadable>());
    });

    test('when opening it fails with something dart:io never raises', () async {
      // **The case that makes `on Object` more than a style choice**, and the
      // one every other test here is blind to: they all raise
      // `FileSystemException`, so narrowing the catch to that type leaves them
      // green while the real production path stays uncovered — `path_provider`
      // raises `MissingPlatformDirectoryException`, which is not a
      // `FileSystemException` and not caught by a catch written for one. An
      // escaped exception would reach a caller holding a [DiagnosticsState] and
      // reasonably treating its three cases as all of them.
      final throwing = FileFetchDiagnosticsStore(
        open: () async => throw const _NotAFileSystemException(),
      );

      expect(await throwing.read('pairing-a'), isA<DiagnosticsUnreadable>());
    });

    test('and when the app has lost permission to read it', () async {
      await store.write(succeeded());
      await Process.run('chmod', <String>['000', file.path]);
      // Asserted rather than assumed: root can read a mode-000 file, and this
      // test would then pass while exercising nothing.
      var unreadable = true;
      try {
        await file.readAsString();
        unreadable = false;
      } on Object {
        // As intended.
      }
      if (!unreadable) {
        markTestSkipped('this runner can read a mode-000 file; nothing to exercise');
        return;
      }

      expect(await file.exists(), isTrue, reason: 'the file is still there');
      expect(await store.read('pairing-a'), isA<DiagnosticsUnreadable>());

      await Process.run('chmod', <String>['600', file.path]);
    });
  });

  group('a record with no pairing to scope it', () {
    test('cannot be constructed, by any of the three constructors', () {
      // The scoping rule is enforced where a record is *made*, not where it is
      // written, because an unscoped record has no meaning to hold in memory
      // either: every fact in it is "under which pairing".
      //
      // **The third case was added because it was measured missing, not for
      // symmetry.** This test read "by either constructor" while a third
      // constructor existed: deleting `_checkPairing` from
      // `FetchDiagnostics.foundNoPublication` left all 570 tests green, where
      // the same deletion on `succeeded` reddens this very test. Two of the
      // three callers were pinned here already and the third was not — and the
      // name of the test was the only thing that said so.
      expect(() => succeeded(pairingId: ''), throwsA(isA<PayloadFormatException>()));
      expect(() => failed(pairingId: ''), throwsA(isA<PayloadFormatException>()));
      expect(
        () => reachedHostWithNothing(pairingId: ''),
        throwsA(isA<PayloadFormatException>()),
      );
    });
  });

  group('the write path commits nothing it could not read back', () {
    test('and today that is a property of the round trip, not of a reachable throw', () async {
      // `write` runs the bytes through `read`'s parser before touching the disk,
      // the `23a` precedent. Here that guard is **unreachable by construction**:
      // every invariant it checks is already enforced by the three
      // constructors, so there is no [FetchDiagnostics] whose encoding the
      // parser would refuse. Saying so beats a test that cannot fail — what is
      // actually pinned is the equivalence the guard exists to keep true, that
      // anything the encoder writes the parser reads back unchanged, and the
      // drift it catches is a future field added to one side only.
      //
      // The list therefore has to cover every constructor, or the equivalence
      // is pinned for the shapes that happen to be listed rather than for the
      // encoder. The `404` shapes were the ones missing.
      //
      // **Measured, and stated as what it is**: on the one mutation available
      // today — `_parse` dropping the carried success on the `404` branch — the
      // group above reddens too, so these two entries are the list's
      // completeness rather than new coverage. What they add is generic:
      // whole-record equality catches a field added to one side of the encoder
      // without anyone remembering to assert it by name, which is the drift
      // this test says it exists for.
      final records = <FetchDiagnostics>[
        succeeded(at: utc(20, 9), seq: '7'),
        succeeded(at: utc(20, 9), seq: '10'),
        failed(at: utc(21, 9), error: FetchFailureClass.transportError),
        failed(
          at: utc(21, 9),
          error: FetchFailureClass.credentialRejected,
          after: FetchSuccess(at: utc(20, 9), seq: PublicationSeq.parse('7')),
        ),
        reachedHostWithNothing(at: utc(21, 9)),
        reachedHostWithNothing(
          at: utc(21, 9),
          after: FetchSuccess(at: utc(20, 9), seq: PublicationSeq.parse('7')),
        ),
      ];

      for (final record in records) {
        await store.write(record);

        expect(held(await store.read('pairing-a')), record);
      }
      expect(
        directory.listSync().map((entry) => entry.uri.pathSegments.last),
        <String>[FileFetchDiagnosticsStore.fileName],
      );
    });
  });
}
