import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/stored_file.dart';

void main() {
  late Directory directory;

  setUp(() async {
    directory = await Directory.systemTemp.createTemp('networth-stored-file-');
  });

  tearDown(() async {
    // Anything left mode-000 would make the temp tree unlistable, so this
    // cannot walk the tree itself to find it — the owner may always chmod.
    Process.runSync('chmod', <String>['-R', '755', directory.path]);
    await directory.delete(recursive: true);
  });

  Future<StoredFile> readAt(String path) => readStoredFile(() async => File(path));

  group('absence is proved, never inferred from exists()', () {
    // Every case below answers `exists() == false`. Only the first is absence,
    // which is why the discriminator cannot be that call — nor the errno, since
    // a dangling symlink raises the same one a missing file does.

    test('nothing at the path is absent, and that is the whole of absent', () async {
      final result = await readAt('${directory.path}/nothing.json');

      expect(result, isA<StoredAbsent>());
      expect(
        await File('${directory.path}/nothing.json').exists(),
        isFalse,
        reason: 'the control: this is the one case exists() classifies correctly',
      );
    });

    test('a directory standing at the path is unreadable', () async {
      Directory('${directory.path}/record.json').createSync();

      final result = await readAt('${directory.path}/record.json');

      expect(result, isA<StoredUnreadable>());
    });

    test('a dangling symlink is unreadable, not absent', () async {
      // The sharp one: opening it raises exactly the errno a missing file
      // raises, so nothing about the failure itself tells them apart. What does
      // is that the name is right there in the directory listing.
      Link('${directory.path}/record.json').createSync('${directory.path}/no-such-target');

      final result = await readAt('${directory.path}/record.json');

      expect(result, isA<StoredUnreadable>());
    });

    test('a real record behind an unsearchable parent is unreadable', () async {
      final locked = Directory('${directory.path}/locked')..createSync();
      final record = File('${locked.path}/record.json')..writeAsStringSync('{"a":1}');
      Process.runSync('chmod', <String>['000', locked.path]);
      // Asserts its own precondition rather than passing vacuously: running as
      // root, or on a filesystem that ignores the mode, the parent stays
      // readable and this test would prove nothing.
      var enforced = true;
      try {
        record.readAsStringSync();
        enforced = false;
      } on FileSystemException {
        // As intended.
      }
      expect(enforced, isTrue, reason: 'mode 000 must actually deny this process');

      final result = await readAt(record.path);

      expect(result, isA<StoredUnreadable>());
    });

    test('a missing containing directory is absence, one level up', () async {
      // A record inside a directory that is not there cannot itself be there.
      final result = await readAt('${directory.path}/gone/record.json');

      expect(result, isA<StoredAbsent>());
    });

    test('a readable file is its bytes, exactly', () async {
      File('${directory.path}/record.json').writeAsStringSync('{"a":1}');

      final result = await readAt('${directory.path}/record.json');

      expect(result, isA<StoredBytes>());
      // Asserted rather than left to the type: every caller hands these bytes
      // straight to a JSON parser that would forgive whitespace, so nothing
      // downstream notices a read that returns almost the file.
      expect((result as StoredBytes).bytes, '{"a":1}');
    });
  });

  test('a file that cannot even be located is unreadable, not absent', () async {
    // `path_provider` raises `MissingPlatformDirectoryException`, which is not a
    // `FileSystemException`. Failing to name the file is not evidence that
    // nothing is stored.
    final result = await readStoredFile(() async => throw const FormatException('no directory'));

    expect(result, isA<StoredUnreadable>());
  });
}
