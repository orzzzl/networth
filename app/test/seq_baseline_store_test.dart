import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/seq_baseline_store.dart';
import 'package:networth_app/src/domain/publication_seq.dart';
import 'package:networth_app/src/domain/seq_baseline.dart';

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
  late FileSeqBaselineStore store;

  setUp(() async {
    directory = await Directory.systemTemp.createTemp('seq-baseline-test');
    file = File('${directory.path}/${FileSeqBaselineStore.fileName}');
    store = FileSeqBaselineStore(open: () async => file);
  });

  tearDown(() async => directory.delete(recursive: true));

  SeqBaseline baseline({
    String pairingId = 'pairing-a',
    String lastSeq = '7',
    DowngradeWarning? warning,
  }) =>
      SeqBaseline(
        pairingId: pairingId,
        lastSeq: PublicationSeq.parse(lastSeq),
        warning: warning,
      );

  group('a pairing with no baseline', () {
    test('reads as absent when nothing has ever been written', () async {
      expect(await store.read('pairing-a'), isA<BaselineAbsent>());
    });

    test('reads as absent when the stored record belongs to another pairing', () async {
      // §9.3 point 3: `last_seq` is scoped to the `pairing_id`, so a record left
      // by the previous pairing is not this pairing's baseline. This is the case
      // that makes §9.3a's restore recoverable by re-pairing.
      await store.write(baseline(pairingId: 'pairing-a', lastSeq: '400'));

      expect(await store.read('pairing-b'), isA<BaselineAbsent>());
      expect(await store.read('pairing-a'), isA<BaselineHeld>());
    });
  });

  group('a stored baseline', () {
    test('round-trips the host\'s own bytes rather than a re-rendered number', () async {
      // '10' is the value a lexicographic comparison gets wrong, and it is also
      // the one a sloppy round-trip would be free to rewrite.
      await store.write(baseline(lastSeq: '10'));

      final state = await store.read('pairing-a');

      expect(state, isA<BaselineHeld>());
      final held = (state as BaselineHeld).baseline;
      expect(held.lastSeq.wire, '10');
      expect(held.lastSeq.value, 10);
      expect(held.warning, isNull);
      expect(jsonDecode(await file.readAsString()), <String, Object?>{
        'pairing_id': 'pairing-a',
        'last_seq': '10',
      });
    });

    test('keeps only the current record when it is replaced', () async {
      await store.write(baseline(lastSeq: '7'));
      await store.write(baseline(lastSeq: '8'));

      final held = (await store.read('pairing-a') as BaselineHeld).baseline;
      expect(held.lastSeq.wire, '8');
      expect(directory.listSync().map((entry) => entry.uri.pathSegments.last),
          <String>[FileSeqBaselineStore.fileName]);
    });

    test('round-trips a downgrade warning with the seq it refused', () async {
      final at = DateTime.utc(2026, 9, 22, 23, 15);
      await store.write(baseline(
        lastSeq: '12',
        warning: DowngradeWarning(
          cause: DowngradeCause.olderPublication,
          at: at,
          refusedSeq: PublicationSeq.parse('7'),
        ),
      ));

      final held = (await store.read('pairing-a') as BaselineHeld).baseline;
      expect(held.warning!.cause, DowngradeCause.olderPublication);
      expect(held.warning!.at.isAtSameMomentAs(at), isTrue);
      expect(held.warning!.refusedSeq!.wire, '7');
    });

    test('round-trips a foreign-pairing warning, which names no refused seq', () async {
      // The refused payload does carry a `seq`, and it belongs to a counter this
      // phone has no baseline for. Storing it beside `last_seq` would put two
      // unrelated numbers in comparable positions.
      await store.write(baseline(
        warning: DowngradeWarning(
          cause: DowngradeCause.foreignPairing,
          at: DateTime.utc(2026, 9, 22, 23, 15),
        ),
      ));

      final held = (await store.read('pairing-a') as BaselineHeld).baseline;
      expect(held.warning!.cause, DowngradeCause.foreignPairing);
      expect(held.warning!.refusedSeq, isNull);
      expect(
        (jsonDecode(await file.readAsString()) as Map<String, Object?>)['warning'],
        isNot(contains('refused_seq')),
      );
    });

    test('stores the cause under a spelling the Dart identifier cannot change', () async {
      await store.write(baseline(
        warning: DowngradeWarning(
          cause: DowngradeCause.olderPublication,
          at: DateTime.utc(2026),
          refusedSeq: PublicationSeq.parse('1'),
        ),
      ));

      final warning = (jsonDecode(await file.readAsString())
          as Map<String, Object?>)['warning']! as Map<String, Object?>;
      expect(warning['cause'], 'OLDER_PUBLICATION');
    });
  });

  group('a damaged baseline is unreadable, never absent', () {
    // The one way I6 can be switched off without anybody noticing: if a file
    // that exists and does not parse read as "nothing stored", §9.3 point 3's
    // accept-on-trust would take whatever is served next as the new baseline,
    // and corrupting the file would be the bypass. Every shape below is a file
    // that exists.
    const Map<String, String> damaged = <String, String>{
      'not JSON at all': 'not json',
      'a JSON array': '[]',
      'a JSON string': '"pairing-a"',
      'no pairing_id': '{"last_seq": "7"}',
      'an empty pairing_id': '{"pairing_id": "", "last_seq": "7"}',
      'a non-string pairing_id': '{"pairing_id": 1, "last_seq": "7"}',
      'no last_seq': '{"pairing_id": "pairing-a"}',
      'a numeric last_seq': '{"pairing_id": "pairing-a", "last_seq": 7}',
      'a last_seq with a leading zero': '{"pairing_id": "pairing-a", "last_seq": "07"}',
      'a non-decimal last_seq': '{"pairing_id": "pairing-a", "last_seq": "7a"}',
      'an empty last_seq': '{"pairing_id": "pairing-a", "last_seq": ""}',
      'a warning that is not an object':
          '{"pairing_id": "pairing-a", "last_seq": "7", "warning": 1}',
      'a warning with an unknown cause': '{"pairing_id": "pairing-a", "last_seq": "7", '
          '"warning": {"cause": "SOMETHING_ELSE", "at": "2026-09-22T23:15:00Z"}}',
      'a warning with no timestamp': '{"pairing_id": "pairing-a", "last_seq": "7", '
          '"warning": {"cause": "FOREIGN_PAIRING"}}',
      'a warning with an unparseable timestamp': '{"pairing_id": "pairing-a", "last_seq": "7", '
          '"warning": {"cause": "FOREIGN_PAIRING", "at": "yesterday"}}',
      'a downgrade warning naming no refused seq': '{"pairing_id": "pairing-a", "last_seq": "7", '
          '"warning": {"cause": "OLDER_PUBLICATION", "at": "2026-09-22T23:15:00Z"}}',
      'a foreign-pairing warning naming a refused seq': '{"pairing_id": "pairing-a", '
          '"last_seq": "7", "warning": {"cause": "FOREIGN_PAIRING", '
          '"at": "2026-09-22T23:15:00Z", "refused_seq": "3"}}',
      'a refused seq with a leading zero': '{"pairing_id": "pairing-a", "last_seq": "7", '
          '"warning": {"cause": "OLDER_PUBLICATION", "at": "2026-09-22T23:15:00Z", '
          '"refused_seq": "03"}}',
    };

    damaged.forEach((description, bytes) {
      test('$description reads as unreadable', () async {
        await file.writeAsString(bytes);

        final state = await store.read('pairing-a');

        expect(state, isA<BaselineUnreadable>(), reason: 'damage must not read as absent');
        expect((state as BaselineUnreadable).reason, isNotEmpty);
      });
    });

    test('and the bytes are left on disk for the next launch', () async {
      await file.writeAsString('not json');

      await store.read('pairing-a');

      expect(await file.readAsString(), 'not json');
    });

    test('and so is a baseline the app cannot get the bytes of at all', () async {
      // The same defect as every case above, reached through I/O instead of
      // through the parser: a file this phone *has* must never be reported as a
      // file it does not have, whatever went wrong. Deterministic everywhere —
      // it needs no permission the test runner might have.
      final throwing = FileSeqBaselineStore(
        open: () async => throw const FileSystemException('no documents directory'),
      );

      expect(await throwing.read('pairing-a'), isA<BaselineUnreadable>());
    });

    test('including when opening it fails with something dart:io never raises', () async {
      // **Added after mutation testing found the gap**, one commit late. The
      // `on Object` in `read` was written deliberately, because `path_provider`
      // raises `MissingPlatformDirectoryException` and that is not a
      // `FileSystemException` — but every test covering that catch raised a
      // `FileSystemException`, so narrowing it back would have left all of them
      // green. A guard whose reason no test can distinguish is unpinned however
      // carefully it was argued for.
      final throwing = FileSeqBaselineStore(
        open: () async => throw const _NotAFileSystemException(),
      );

      expect(await throwing.read('pairing-a'), isA<BaselineUnreadable>());
    });

    test('and so is one the app has lost permission to read', () async {
      await store.write(baseline(lastSeq: '7'));
      await Process.run('chmod', <String>['000', file.path]);
      // Asserted rather than assumed: root can read a 000 file, and this test
      // would then pass while exercising nothing. The precondition is the whole
      // test, so it is checked instead of hoped for.
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
      expect(await store.read('pairing-a'), isA<BaselineUnreadable>());

      await Process.run('chmod', <String>['600', file.path]);
    });

    test('and so is anything at the path that is not a readable file', () async {
      // §9.3's accept-on-trust is for a baseline that never existed. Each shape
      // below answers `exists() == false` while being something other than
      // absence, and reading any of them as [BaselineAbsent] would re-baseline
      // I6 on whatever the next payload claims — corruption as the bypass for
      // the one check that survives a valid ciphertext.
      Directory(file.path).createSync();
      expect(await store.read('pairing-a'), isA<BaselineUnreadable>(),
          reason: 'a directory standing at the path');
      Directory(file.path).deleteSync();

      Link(file.path).createSync('${directory.path}/no-such-target');
      expect(await store.read('pairing-a'), isA<BaselineUnreadable>(),
          reason: 'a dangling symlink, which fails with a missing file\'s own errno');
      Link(file.path).deleteSync();

      final locked = Directory('${directory.path}/locked')..createSync();
      // Registered before the mode changes, so a failure below cannot leave the
      // temp tree undeletable and mask this test behind a tearDown error.
      addTearDown(() => Process.run('chmod', <String>['-R', '755', directory.path]));
      final behind = FileSeqBaselineStore(
        open: () async => File('${locked.path}/${FileSeqBaselineStore.fileName}'),
      );
      await behind.write(baseline(lastSeq: '7'));
      await Process.run('chmod', <String>['000', locked.path]);
      var enforced = true;
      try {
        await File('${locked.path}/${FileSeqBaselineStore.fileName}').readAsString();
        enforced = false;
      } on Object {
        // As intended.
      }
      if (enforced) {
        expect(await behind.read('pairing-a'), isA<BaselineUnreadable>(),
            reason: 'a perfectly good baseline behind a parent it cannot search');
      } else {
        markTestSkipped('this runner can search a mode-000 directory');
      }
      await Process.run('chmod', <String>['755', locked.path]);
    });

    test('even when the damaged record names another pairing', () async {
      // The scoping check runs before the rest of the record is read, so a
      // foreign pairing is absent — but a file whose `pairing_id` cannot be read
      // at all cannot be scoped away, because "which pairing is this for" is
      // exactly the question it failed to answer.
      await file.writeAsString('{"pairing_id": 1, "last_seq": "7"}');

      expect(await store.read('pairing-b'), isA<BaselineUnreadable>());
    });
  });

  group('writing refuses to commit what it could not read back', () {
    test('and leaves the previous record standing', () async {
      await store.write(baseline(lastSeq: '7'));

      await expectLater(
        store.write(baseline(pairingId: '', lastSeq: '8')),
        throwsA(isA<Exception>()),
      );

      final held = (await store.read('pairing-a') as BaselineHeld).baseline;
      expect(held.lastSeq.wire, '7');
      expect(directory.listSync().map((entry) => entry.uri.pathSegments.last),
          <String>[FileSeqBaselineStore.fileName]);
    });
  });
}
