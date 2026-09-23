import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/clock_anchor_store.dart';
import 'package:networth_app/src/domain/clock_anchor.dart';
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
  late FileClockAnchorStore store;

  setUp(() async {
    directory = await Directory.systemTemp.createTemp('clock-anchor-test');
    file = File('${directory.path}/${FileClockAnchorStore.fileName}');
    store = FileClockAnchorStore(open: () async => file);
  });

  tearDown(() async => directory.delete(recursive: true));

  DateTime utc(int day, int hour) => DateTime.utc(2026, 9, day, hour);

  ClockAnchor anchor({
    String pairingId = 'pairing-a',
    DateTime? at,
    String runId = 'run-1',
    Duration elapsed = const Duration(minutes: 3),
    String seq = '7',
  }) =>
      ClockAnchor(
        pairingId: pairingId,
        anchoredAt: at ?? utc(20, 9),
        reading: MonotonicReading(runId: runId, elapsed: elapsed),
        seq: PublicationSeq.parse(seq),
      );

  ClockAnchor held(AnchorState state) {
    expect(state, isA<AnchorHeld>());
    return (state as AnchorHeld).anchor;
  }

  Future<void> writeRaw(Object? json) => file.writeAsString(jsonEncode(json));

  group('what the anchor value type refuses to be', () {
    test('an anchor with no pairing is not scoped to anything', () {
      expect(
        () => anchor(pairingId: ''),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('an anchor with no run id cannot be compared to any later reading', () {
      expect(() => anchor(runId: ''), throwsA(isA<PayloadFormatException>()));
    });

    test('a run cannot have been going for a negative time', () {
      expect(
        () => anchor(elapsed: const Duration(seconds: -1)),
        throwsA(isA<PayloadFormatException>()),
      );
    });
  });

  group('round trip', () {
    test('an established anchor reads back as the same value', () async {
      final written = anchor(at: utc(20, 9), runId: 'run-x', elapsed: const Duration(hours: 2));
      await store.establish(written);
      expect(held(await store.read('pairing-a')), written);
    });

    test('the stored form is the format, not whatever the encoder happens to emit', () async {
      // Write and read consult the same code, so a self-consistent rename of any
      // key round-trips perfectly and no round-trip test can see it. What it
      // breaks is on the far side of an app update: a build that spells a key
      // differently reads its own record as damaged. So the literals are
      // asserted as a format.
      await store.establish(
        anchor(
          pairingId: 'pairing-a',
          at: DateTime.utc(2026, 9, 20, 9, 30),
          runId: 'run-x',
          elapsed: const Duration(milliseconds: 1500),
          seq: '41',
        ),
      );
      expect(
        jsonDecode(await file.readAsString()),
        <String, Object?>{
          'pairing_id': 'pairing-a',
          'anchored_at': '2026-09-20T09:30:00.000Z',
          'run_id': 'run-x',
          'monotonic_elapsed_ms': 1500,
          'seq': '41',
        },
      );
    });

    test('an instant given in local time is stored with its zone', () async {
      final local = DateTime.utc(2026, 9, 20, 9).toLocal();
      await store.establish(anchor(at: local));
      expect(held(await store.read('pairing-a')).anchoredAt.isUtc, isTrue);
      expect(await file.readAsString(), contains('2026-09-20T09:00:00.000Z'));
    });
  });

  group('absence and damage are different answers', () {
    test('no file at all is absence', () async {
      expect(await store.read('pairing-a'), isA<AnchorAbsent>());
    });

    test('an anchor for another pairing is absence, not damage', () async {
      await store.establish(anchor(pairingId: 'pairing-a'));
      expect(await store.read('pairing-b'), isA<AnchorAbsent>());
    });

    test('bytes that are not JSON are unreadable', () async {
      await file.writeAsString('{not json');
      expect(await store.read('pairing-a'), isA<AnchorUnreadable>());
    });

    test('JSON that is not an object is unreadable', () async {
      await writeRaw(<Object?>['anchor']);
      expect(await store.read('pairing-a'), isA<AnchorUnreadable>());
    });

    test('a record with no pairing is unreadable, not a foreign one', () async {
      await writeRaw(<String, Object?>{
        'anchored_at': '2026-09-20T09:00:00.000Z',
        'run_id': 'run-1',
        'monotonic_elapsed_ms': 0,
        'seq': '7',
      });
      expect(await store.read('pairing-a'), isA<AnchorUnreadable>());
    });

    test('an empty pairing is damage, not somebody else s anchor', () async {
      // An empty string compares unequal to every real pairing, so a record
      // that reached the scoping check would be filed as a foreign anchor and
      // reported as absence — a damaged record spelling itself as a clean first
      // run.
      await writeRaw(<String, Object?>{
        'pairing_id': '',
        'anchored_at': '2026-09-20T09:00:00.000Z',
        'run_id': 'run-1',
        'monotonic_elapsed_ms': 0,
        'seq': '7',
      });
      expect(await store.read('pairing-a'), isA<AnchorUnreadable>());
    });

    test('an empty run id is unreadable, so no later reading can match it', () async {
      await writeRaw(<String, Object?>{
        'pairing_id': 'pairing-a',
        'anchored_at': '2026-09-20T09:00:00.000Z',
        'run_id': '',
        'monotonic_elapsed_ms': 0,
        'seq': '7',
      });
      expect(await store.read('pairing-a'), isA<AnchorUnreadable>());
    });

    test('a bare instant with no timezone is refused, not read as local time', () async {
      // The anchored instant is subtracted from `device_now` to get the wall
      // side of the drift comparison. Read as local time on this owner's phone
      // it lands seven or eight hours off — orders of magnitude past the
      // two-second tolerance, so every verdict would be unknown for a reason
      // that has nothing to do with the clock.
      await writeRaw(<String, Object?>{
        'pairing_id': 'pairing-a',
        'anchored_at': '2026-09-20T09:00:00.000',
        'run_id': 'run-1',
        'monotonic_elapsed_ms': 0,
        'seq': '7',
      });
      expect(await store.read('pairing-a'), isA<AnchorUnreadable>());
    });

    test('a record with no run id is unreadable', () async {
      await writeRaw(<String, Object?>{
        'pairing_id': 'pairing-a',
        'anchored_at': '2026-09-20T09:00:00.000Z',
        'monotonic_elapsed_ms': 0,
        'seq': '7',
      });
      expect(await store.read('pairing-a'), isA<AnchorUnreadable>());
    });

    test('a non-integer monotonic reading is unreadable', () async {
      await writeRaw(<String, Object?>{
        'pairing_id': 'pairing-a',
        'anchored_at': '2026-09-20T09:00:00.000Z',
        'run_id': 'run-1',
        'monotonic_elapsed_ms': '0',
        'seq': '7',
      });
      expect(await store.read('pairing-a'), isA<AnchorUnreadable>());
    });

    test('a negative monotonic reading is unreadable', () async {
      await writeRaw(<String, Object?>{
        'pairing_id': 'pairing-a',
        'anchored_at': '2026-09-20T09:00:00.000Z',
        'run_id': 'run-1',
        'monotonic_elapsed_ms': -1,
        'seq': '7',
      });
      expect(await store.read('pairing-a'), isA<AnchorUnreadable>());
    });

    test('a seq the payload layer would refuse is refused here too', () async {
      await writeRaw(<String, Object?>{
        'pairing_id': 'pairing-a',
        'anchored_at': '2026-09-20T09:00:00.000Z',
        'run_id': 'run-1',
        'monotonic_elapsed_ms': 0,
        'seq': '007',
      });
      expect(await store.read('pairing-a'), isA<AnchorUnreadable>());
    });

    test('a file that cannot be located is unreadable, never absent', () async {
      final broken = FileClockAnchorStore(
        open: () async => throw const _NotAFileSystemException(),
      );
      expect(await broken.read('pairing-a'), isA<AnchorUnreadable>());
    });

    test('a file that is there and cannot be read is unreadable, never absent', () async {
      // Locating succeeds and the read fails, which is the other branch: a
      // directory standing where the record should be raises a different errno
      // from a missing file and is still not absence, because the name is in
      // its parent's listing.
      await Directory(file.path).create(recursive: true);
      expect(await store.read('pairing-a'), isA<AnchorUnreadable>());
    });
  });

  group('establish checks the previous anchor before replacing it', () {
    test('the first anchor under a pairing needs nothing to advance', () async {
      await store.establish(anchor(seq: '41'));
      expect(held(await store.read('pairing-a')).seq, PublicationSeq.parse('41'));
    });

    test('a newer publication moves the anchor', () async {
      await store.establish(anchor(at: utc(20, 9), seq: '41'));
      await store.establish(anchor(at: utc(21, 9), seq: '42'));
      final stored = held(await store.read('pairing-a'));
      expect(stored.seq, PublicationSeq.parse('42'));
      expect(stored.anchoredAt, utc(21, 9));
    });

    test('an unchanged publication is refused and leaves the anchor where it was', () async {
      // Codex's boundary, and the reason the whole guard exists: a fetch that
      // returns the publication we already hold changes nothing about when that
      // copy arrived, so moving the anchor to now would grant trust over an
      // interval shorter than the copy's real age.
      await store.establish(anchor(at: utc(20, 9), seq: '41'));
      await expectLater(
        store.establish(anchor(at: utc(29, 9), seq: '41')),
        throwsA(isA<AnchorNotAdvanced>()),
      );
      expect(held(await store.read('pairing-a')).anchoredAt, utc(20, 9));
    });

    test('an older publication is refused too', () async {
      await store.establish(anchor(at: utc(20, 9), seq: '41'));
      await expectLater(
        store.establish(anchor(at: utc(29, 9), seq: '40')),
        throwsA(isA<AnchorNotAdvanced>()),
      );
      expect(held(await store.read('pairing-a')).anchoredAt, utc(20, 9));
    });

    test('the comparison is numeric, not lexicographic', () async {
      // `'9' > '10'` as strings. A string comparison here would refuse the tenth
      // publication's anchor and pin the phone to the ninth one's instant.
      await store.establish(anchor(at: utc(20, 9), seq: '9'));
      await store.establish(anchor(at: utc(21, 9), seq: '10'));
      expect(held(await store.read('pairing-a')).seq, PublicationSeq.parse('10'));
    });

    test('an anchor under a different pairing does not block this one', () async {
      await store.establish(anchor(pairingId: 'pairing-a', seq: '41'));
      await store.establish(anchor(pairingId: 'pairing-b', seq: '3'));
      expect(held(await store.read('pairing-b')).seq, PublicationSeq.parse('3'));
    });

    test('a damaged anchor does not block recovery', () async {
      // Refusing here would leave a phone whose anchor file was corrupted
      // permanently unable to date any copy. It is safe because until this call
      // lands the store answers unreadable, which is already `COPY_UNKNOWN`.
      await file.writeAsString('{not json');
      await store.establish(anchor(seq: '41'));
      expect(held(await store.read('pairing-a')).seq, PublicationSeq.parse('41'));
    });
  });
}
