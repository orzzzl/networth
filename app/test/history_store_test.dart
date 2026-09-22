import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/history_store.dart';
import 'package:networth_app/src/domain/payload_format_exception.dart';
import 'package:networth_app/src/domain/phone_payload.dart';

import 'fixtures.dart';

/// A payload in the real shape, with the three fields a reading is made of
/// overridden.
///
/// Built from the shipped fixture rather than hand-assembled: the store's whole
/// job is writing down what the daemon sent, so a test that invents the payload
/// shape would be checking the store against a contract nobody publishes.
PhonePayload payload({
  required String publishedAt,
  String seq = '1',
  int valueMinor = 4250000,
  bool isComplete = true,
  String pairingId = 'fixture-pairing',
}) {
  final body = jsonDecode(readFixture(knownFixture)) as Map<String, Object?>;
  final total = Map<String, Object?>.from(body['total']! as Map<String, Object?>);
  total['value_minor'] = valueMinor;
  total['is_complete'] = isComplete;
  return PhonePayload.fromJson(<String, Object?>{
    ...body,
    'published_at': publishedAt,
    'seq': seq,
    'pairing_id': pairingId,
    'total': total,
  });
}

void main() {
  late Directory directory;
  late File file;

  setUp(() {
    directory = Directory.systemTemp.createTempSync('networth-history');
    file = File('${directory.path}/${FileHistoryStore.fileName}');
  });

  tearDown(() => directory.deleteSync(recursive: true));

  /// A store over the same directory — the next launch, from cold.
  FileHistoryStore launch({int retainedDays = FileHistoryStore.defaultRetainedDays}) =>
      FileHistoryStore(open: () async => file, retainedDays: retainedDays);

  List<Map<String, Object?>> readingsOnDisk() =>
      (jsonDecode(file.readAsStringSync()) as List<Object?>)
          .cast<Map<String, Object?>>();

  group('an accepted payload is recorded, as the host stored it', () {
    test('one reading per payload, holding the numbers the host published', () async {
      await launch().record(payload(publishedAt: '2026-09-15T04:00:00Z', seq: '41'));

      expect(readingsOnDisk(), hasLength(1));
      final reading = readingsOnDisk().single;
      expect(DateTime.parse(reading['published_at']! as String),
          DateTime.utc(2026, 9, 15, 4));
      expect(reading['seq'], '41');
      expect(reading['total'], {
        'value_minor': 4250000,
        'assets_minor': 5000000,
        'liabilities_minor': 750000,
        'currency': 'USD',
        'static_account_count': 0,
        'is_complete': true,
        'age_state': 'KNOWN',
        'as_of': '2026-09-14T20:15:00.000Z',
      });
    });

    test('nothing is derived from another reading', () async {
      // The sharp end of it: whatever ends up on disk, every amount there is one
      // that arrived in a payload. A store that averaged, smoothed or carried a
      // value forward would put a number in the record that was never published.
      final store = launch();
      for (final (day, value) in [(10, 100), (11, 250), (13, 90)]) {
        await store.record(
          payload(publishedAt: '2026-09-${day}T04:00:00Z', seq: '$day', valueMinor: value),
        );
      }

      expect(
        [
          for (final reading in readingsOnDisk())
            (reading['total']! as Map<String, Object?>)['value_minor'],
        ],
        [100, 250, 90],
      );
    });

    test('the file it writes is a series the curve can render', () async {
      // The two halves are checked against each other rather than against two
      // copies of one format: these bytes go back through `parseHistory`, the
      // same parser the screen uses.
      final store = launch();
      await store.record(payload(publishedAt: '2026-09-10T04:00:00Z', valueMinor: 100));
      await store.record(
        payload(publishedAt: '2026-09-12T04:00:00Z', valueMinor: 250, isComplete: false),
      );

      final history = await store.load();

      expect(history.points.map((point) => point.day),
          [DateTime.utc(2026, 9, 10), DateTime.utc(2026, 9, 12)]);
      expect(history.points.map((point) => point.total.amount.minorUnits), [100, 250]);
      expect(history.hasGap, isTrue, reason: '09-11 was never recorded');
      expect(history.hasIncompletePoint, isTrue);
    });

    test('a first launch has recorded nothing, and says so without writing', () async {
      expect((await launch().load()).isEmpty, isTrue);
      expect(file.existsSync(), isFalse, reason: 'reading the record created one');
    });
  });

  group('durable across launches', () {
    test('a reading recorded by one launch is there for the next', () async {
      await launch().record(payload(publishedAt: '2026-09-10T04:00:00Z', valueMinor: 777));

      final history = await launch().load();

      expect(history.points.single.total.amount.minorUnits, 777);
    });

    test('and the record outlives the pairing that fetched it', () async {
      // §6.3: the payload key is revocable and re-pairing replaces it. The
      // owner's own curve is not a capability, so it must survive that — which
      // it does by never being identified by the pairing at all.
      await launch().record(
        payload(publishedAt: '2026-09-10T04:00:00Z', pairingId: 'pairing-before'),
      );
      await launch().record(
        payload(publishedAt: '2026-09-11T04:00:00Z', pairingId: 'pairing-after'),
      );

      expect((await launch().load()).points, hasLength(2));
      expect(
        file.readAsStringSync(),
        isNot(anyOf(contains('pairing'), contains('airing_id'))),
        reason: 'the record named the pairing it arrived under',
      );
    });
  });

  group('a later payload never mutates an earlier point', () {
    test('revaluing in 2026 leaves the 2024 reading byte-identical', () async {
      final store = launch();
      await store.record(payload(publishedAt: '2024-03-02T04:00:00Z', valueMinor: 111));
      final before = jsonEncode(readingsOnDisk().single);

      await store.record(payload(publishedAt: '2026-09-15T04:00:00Z', valueMinor: 999));

      expect(readingsOnDisk(), hasLength(2));
      expect(jsonEncode(readingsOnDisk().first), before);
    });

    test('a second reading of the same day replaces that day and no other', () async {
      // §7's rule — "latest per day for the curve" — applied where the bytes
      // are. It is a selection among published readings, never an arithmetic.
      final store = launch();
      await store.record(payload(publishedAt: '2026-09-10T04:00:00Z', valueMinor: 100));
      await store.record(payload(publishedAt: '2026-09-11T04:00:00Z', valueMinor: 200));
      await store.record(
        payload(publishedAt: '2026-09-11T19:30:00Z', seq: '2', valueMinor: 250),
      );

      final history = await store.load();

      expect(history.points.map((point) => point.total.amount.minorUnits), [100, 250]);
      expect(history.points.last.publishedAt, DateTime.utc(2026, 9, 11, 19, 30));
    });

    test('an out-of-order arrival does not displace a later reading', () async {
      // I6 (§9.3) is the real defence and it lives at the fetch, not here. This
      // is the store not relying on that having happened first.
      final store = launch();
      await store.record(
        payload(publishedAt: '2026-09-11T19:30:00Z', seq: '2', valueMinor: 250),
      );
      await store.record(
        payload(publishedAt: '2026-09-11T04:00:00Z', seq: '1', valueMinor: 200),
      );

      expect((await store.load()).points.single.total.amount.minorUnits, 250);
    });

    test('recording the same payload again writes nothing at all', () async {
      // The common case, not an edge one: the app is opened five times before
      // the host publishes anything new. Without this the record would grow with
      // app launches, and every launch would rewrite the whole file.
      final store = launch();
      final same = payload(publishedAt: '2026-09-10T04:00:00Z', seq: '41');
      await store.record(same);
      final written = file.statSync().modified;
      final bytes = file.readAsStringSync();

      await store.record(same);
      await store.record(same);

      expect(readingsOnDisk(), hasLength(1));
      expect(file.readAsStringSync(), bytes);
      expect(file.statSync().modified, written, reason: 'the file was rewritten');
    });
  });

  group('bounded', () {
    test('the oldest days fall out of the window', () async {
      final store = launch(retainedDays: 3);
      for (final day in [10, 11, 12, 13, 14]) {
        await store.record(
          payload(publishedAt: '2026-09-${day}T04:00:00Z', seq: '$day', valueMinor: day),
        );
      }

      expect((await store.load()).points.map((point) => point.total.amount.minorUnits),
          [12, 13, 14]);
    });

    test('the window counts days, so app launches cannot fill it', () async {
      // The bound is what stops an unbounded local copy of the owner's net
      // worth accumulating on the phone (§6.2). A reading per *fetch* would
      // make the window's size depend on how often he opens the app.
      final store = launch(retainedDays: 3);
      for (var hour = 0; hour < 20; hour++) {
        await store.record(
          payload(publishedAt: '2026-09-10T${hour.toString().padLeft(2, '0')}:00:00Z',
              seq: '$hour'),
        );
      }

      expect(readingsOnDisk(), hasLength(1));
    });

    test('the default window is 400 days, and it is the shipped one', () {
      // A number changed to something convenient in a test would otherwise
      // never be noticed; `appPrivate` is what the app actually constructs.
      expect(FileHistoryStore.defaultRetainedDays, 400);
      expect(FileHistoryStore.appPrivate().retainedDays, 400);
    });
  });

  group('a record it cannot read is never a record it deletes', () {
    test('recording onto a corrupt file refuses, and leaves the bytes alone', () async {
      file.writeAsStringSync('{ not a series');

      await expectLater(
        launch().record(payload(publishedAt: '2026-09-10T04:00:00Z')),
        throwsA(isA<PayloadFormatException>()),
      );
      expect(file.readAsStringSync(), '{ not a series');
    });

    test('and loading it is an unreadable series, not an empty one', () async {
      // Two different facts, and `HomePage` renders them differently: "no
      // readings recorded yet" over a record that exists and failed to load
      // would be a false statement about the owner's own history.
      file.writeAsStringSync('[{"published_at": "2026-09-10T04:00:00Z"}]');

      await expectLater(launch().load(), throwsA(isA<PayloadFormatException>()));
    });

    test('a reading with no seq is refused rather than half-read', () async {
      file.writeAsStringSync(jsonEncode([
        {'published_at': '2026-09-10T04:00:00Z', 'total': <String, Object?>{}},
      ]));

      await expectLater(
        launch().record(payload(publishedAt: '2026-09-11T04:00:00Z')),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('the record is replaced, never opened for writing in place', () async {
      // What temp+rename buys: `history.json` is never the file being
      // truncated, so a process death mid-write costs the reading being added
      // rather than every reading there is.
      //
      // Made observable by taking write permission off the file itself — a
      // rename into the directory still works, an in-place write does not.
      // **The environment has to be able to enforce that**, and as root it
      // cannot, so the first thing checked is that a direct write really is
      // refused here. Without that this test would pass vacuously wherever
      // permissions are ignored, against the bug as happily as against the fix.
      await launch().record(payload(publishedAt: '2026-09-10T04:00:00Z'));
      Process.runSync('chmod', ['444', file.path]);
      var inPlaceWritesRefused = false;
      try {
        file.openSync(mode: FileMode.append).closeSync();
      } on FileSystemException {
        inPlaceWritesRefused = true;
      }
      if (!inPlaceWritesRefused) {
        markTestSkipped('this user can write a read-only file; nothing to measure');
        return;
      }

      await launch().record(payload(publishedAt: '2026-09-11T04:00:00Z'));

      expect((await launch().load()).points, hasLength(2));
    });

    test('a half-written file is not what the next launch reads', () async {
      // The write goes through a temporary name and a rename, so the record is
      // never the thing being truncated. What a crash mid-write can leave is
      // this leftover, and it must not be mistaken for the record.
      File('${file.path}.writing').writeAsStringSync('[garbage');
      await launch().record(payload(publishedAt: '2026-09-10T04:00:00Z'));

      expect((await launch().load()).points, hasLength(1));
    });
  });
}
