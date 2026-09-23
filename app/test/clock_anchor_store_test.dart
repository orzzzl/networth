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

  /// [trust] defaults to what **production** passes, because nothing in the app
  /// can construct the other value; a test that wants a corroborated anchor has
  /// to say so, which is the point.
  ClockAnchor anchor({
    String pairingId = 'pairing-a',
    DateTime? at,
    String runId = 'run-1',
    Duration elapsed = const Duration(minutes: 3),
    String seq = '7',
    AnchorTrust trust = AnchorTrust.unproven,
  }) =>
      ClockAnchor(
        pairingId: pairingId,
        anchoredAt: at ?? utc(20, 9),
        reading: MonotonicReading(runId: runId, elapsed: elapsed),
        seq: PublicationSeq.parse(seq),
        trust: trust,
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
          'trust': 'unproven',
        },
      );
    });

    test('the trust spelling is the format too, and both values are asserted', () async {
      // `AnchorTrust.name` would have made a Dart rename a silent format change,
      // readable only by the build that did the renaming. Both values are
      // written out because a table shared by the encoder and the decoder is
      // invisible to any round trip through it.
      await store.establish(anchor(seq: '41', trust: AnchorTrust.corroborated));
      expect((jsonDecode(await file.readAsString()) as Map)['trust'], 'corroborated');
      // A fresh start rather than a second establish, which would *inherit* the
      // corroborated value over an interval both clocks agree on.
      await file.delete();
      await store.establish(anchor(seq: '41'));
      expect((jsonDecode(await file.readAsString()) as Map)['trust'], 'unproven');
    });

    test('a record that does not say what it is worth is damaged, not unproven', () async {
      // Defaulting a missing field to the safe value reads as prudence and
      // hides the case the format literals exist to catch: a build that spelled
      // the key differently. Unreadable is already `COPY_UNKNOWN`, so saying so
      // costs nothing.
      await writeRaw(<String, Object?>{
        'pairing_id': 'pairing-a',
        'anchored_at': '2026-09-20T09:00:00.000Z',
        'run_id': 'run-1',
        'monotonic_elapsed_ms': 0,
        'seq': '7',
      });
      expect(await store.read('pairing-a'), isA<AnchorUnreadable>());
    });

    test('a trust value this build does not know is damaged', () async {
      await writeRaw(<String, Object?>{
        'pairing_id': 'pairing-a',
        'anchored_at': '2026-09-20T09:00:00.000Z',
        'run_id': 'run-1',
        'monotonic_elapsed_ms': 0,
        'seq': '7',
        'trust': 'probably-fine',
      });
      expect(await store.read('pairing-a'), isA<AnchorUnreadable>());
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

  group('an advance carries the old anchor s trust and cannot manufacture it', () {
    Future<AnchorTrust> trustAfter(List<ClockAnchor> anchors) async {
      for (final one in anchors) {
        await store.establish(one);
      }
      return held(await store.read('pairing-a')).trust;
    }

    test('the first anchor under a pairing is unproven, whatever it anchors', () async {
      // The record written here is two readings taken a moment apart. They
      // agree with each other no matter how wrong the wall clock is, so there
      // is nothing in it to believe.
      expect(await trustAfter(<ClockAnchor>[anchor(seq: '41')]), AnchorTrust.unproven);
    });

    test('recovery from a damaged record is unproven, not restored', () async {
      // The damaged record may have been the one holding the evidence of a
      // broken clock. Recovery restores the ability to anchor, never the right
      // to be believed.
      await file.writeAsString('{not json');
      expect(await trustAfter(<ClockAnchor>[anchor(seq: '41')]), AnchorTrust.unproven);
    });

    test('an advance over an unproven anchor stays unproven on a perfect interval', () async {
      // Two hours by both clocks, same run, zero drift — and still nothing,
      // because a perfectly measured interval since an unproven instant is an
      // unproven instant.
      expect(
        await trustAfter(<ClockAnchor>[
          anchor(at: utc(20, 9), elapsed: Duration.zero, seq: '41'),
          anchor(at: utc(20, 11), elapsed: const Duration(hours: 2), seq: '42'),
        ]),
        AnchorTrust.unproven,
      );
    });

    test('an advance over a corroborated anchor keeps it when the interval holds', () async {
      expect(
        await trustAfter(<ClockAnchor>[
          anchor(
            at: utc(20, 9),
            elapsed: Duration.zero,
            seq: '41',
            trust: AnchorTrust.corroborated,
          ),
          anchor(at: utc(20, 11), elapsed: const Duration(hours: 2), seq: '42'),
        ]),
        AnchorTrust.corroborated,
      );
    });

    test('a measured rollback is not erased by the publication that advances seq', () async {
      // The review case, at this layer. The phone anchored seq 41, spent nine
      // days suspended, and had its clock corrected backwards — so one hour of
      // wall time stands against nine days of real time. Then it fetches seq
      // 42, which the host published before it stopped. Re-stamping both
      // readings would leave them in perfect agreement and hand a nine-day-old
      // publication a fresh verdict.
      expect(
        await trustAfter(<ClockAnchor>[
          anchor(
            at: utc(20, 9),
            elapsed: Duration.zero,
            seq: '41',
            trust: AnchorTrust.corroborated,
          ),
          anchor(at: utc(20, 10), elapsed: const Duration(days: 9), seq: '42'),
        ]),
        AnchorTrust.unproven,
      );
    });

    test('an advance from a different run cannot inherit anything', () async {
      // A counter that reset. The two readings are both valid and entirely
      // incomparable, so the interval between them was never measured.
      expect(
        await trustAfter(<ClockAnchor>[
          anchor(
            at: utc(20, 9),
            runId: 'run-1',
            elapsed: Duration.zero,
            seq: '41',
            trust: AnchorTrust.corroborated,
          ),
          anchor(at: utc(20, 11), runId: 'run-2', elapsed: const Duration(hours: 2), seq: '42'),
        ]),
        AnchorTrust.unproven,
      );
    });

    test('a counter that went backwards under one run id inherits nothing', () async {
      // Same run, smaller reading: one of the two is wrong and there is no way
      // to tell which, so the run identity has stopped meaning anything.
      //
      // **The two readings must very nearly cancel**, and that is the whole
      // point of the case. Deleting the guard and letting a negative monotonic
      // delta through leaves `drift = wall - monotonic`, which for a backwards
      // counter *grows* — so any ordinary scenario is caught by the drift check
      // anyway and cannot tell whether this guard exists. Written first with
      // three hours of each, it passed against the mutant. One second of wall
      // and two of counter leaves a drift of one second, inside
      // `ContinuityHeld.tolerance`, and the mutant hands back corroborated on
      // two readings that flatly contradict each other.
      expect(
        await trustAfter(<ClockAnchor>[
          anchor(
            at: DateTime.utc(2026, 9, 20, 9),
            elapsed: const Duration(hours: 5),
            seq: '41',
            trust: AnchorTrust.corroborated,
          ),
          anchor(
            at: DateTime.utc(2026, 9, 20, 8, 59, 59),
            elapsed: const Duration(hours: 4, minutes: 59, seconds: 58),
            seq: '42',
          ),
        ]),
        AnchorTrust.unproven,
      );
    });

    test('a caller that corroborates this stamp does not need the old one', () async {
      // Fresh proof outranks history — otherwise a phone whose clock was once
      // wrong could never recover, no matter what it later established.
      expect(
        await trustAfter(<ClockAnchor>[
          anchor(at: utc(20, 9), elapsed: Duration.zero, seq: '41'),
          anchor(
            at: utc(20, 10),
            elapsed: const Duration(days: 9),
            seq: '42',
            trust: AnchorTrust.corroborated,
          ),
        ]),
        AnchorTrust.corroborated,
      );
    });
  });
}
