import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/history_store.dart';
import 'package:networth_app/src/data/snapshot_source.dart';
import 'package:networth_app/src/domain/net_worth_history.dart';
import 'package:networth_app/src/domain/phone_payload.dart';

import 'fixtures.dart';
import 'history_store_test.dart' show payload;

/// A source in the shape task 22's will be: it yields a payload and says the
/// payload is real.
class _RealSource implements SnapshotSource {
  _RealSource(this.value);

  final PhonePayload value;

  @override
  bool get isSynthetic => false;

  @override
  Future<PhonePayload> load() async => value;
}

/// A store that cannot be written — a full disk, a revoked directory.
class _BrokenStore implements HistoryStore {
  @override
  Future<NetWorthHistory> load() async => NetWorthHistory.empty;

  @override
  Future<void> record(PhonePayload payload) async =>
      throw const FileSystemException('no space left on device');
}

void main() {
  late Directory directory;
  late FileHistoryStore store;

  setUp(() {
    directory = Directory.systemTemp.createTempSync('networth-recording');
    store = storeIn(directory);
  });

  tearDown(() => directory.deleteSync(recursive: true));

  test('a payload the app accepts is a payload the app records', () async {
    final source = RecordingSnapshotSource(
      inner: _RealSource(payload(publishedAt: '2026-09-15T04:00:00Z', valueMinor: 4250000)),
      store: store,
    );

    final returned = await source.load();

    expect(returned.seq, '1');
    // Awaited rather than fired and forgotten: the read that follows a load
    // sees that load's own write, which is what lets one screen show the point
    // it just fetched.
    expect((await store.load()).points.single.total.amount.minorUnits, 4250000);
  });

  group('a synthetic source is passed through and never recorded', () {
    // The hazard task 23a is named after, arriving through the door marked
    // *record*. A fixture reading written into the store would sit there at the
    // fixture's own `published_at` forever, and once real readings arrived
    // nothing on the curve could tell them apart.
    test('the fixture wiring this build ships records nothing', () async {
      final source = RecordingSnapshotSource(
        inner: FixtureSnapshotSource(knownFixture, bundle: StringAssetBundle.ofFixtures()),
        store: store,
      );

      final returned = await source.load();

      expect(returned.total.amount.minorUnits, 4250000,
          reason: 'the payload must still reach the screen');
      expect((await store.load()).isEmpty, isTrue);
      expect(
        File('${directory.path}/${FileHistoryStore.fileName}').existsSync(),
        isFalse,
      );
    });

    test('and the control: the same wrapper does record a real one', () async {
      // Without this, "the store is empty" would also be satisfied by a wrapper
      // that records nothing at all, and the refusal would be indistinguishable
      // from a broken recorder.
      final source = RecordingSnapshotSource(
        inner: _RealSource(payload(publishedAt: '2026-09-15T04:00:00Z')),
        store: store,
      );

      await source.load();

      expect((await store.load()).points, hasLength(1));
    });

    test('a source that cannot say which kind it is does not compile', () {
      // Not a runtime assertion — `SnapshotSource.isSynthetic` is an interface
      // member with no default, so a source added later has to declare it. This
      // test records *why* that member is required, since the property it
      // guards is invisible in any single file.
      expect(FixtureSnapshotSource(knownFixture).isSynthetic, isTrue);
      expect(_RealSource(payload(publishedAt: '2026-09-15T04:00:00Z')).isSynthetic, isFalse);
    });
  });

  test('a store that will not write does not take the total off the screen', () async {
    // The number with its age is what this product is for; the curve is what it
    // adds. Same rule `HomePage` applies to an unreadable series, one layer
    // down, because the write happens before anything is rendered.
    final source = RecordingSnapshotSource(
      inner: _RealSource(payload(publishedAt: '2026-09-15T04:00:00Z')),
      store: _BrokenStore(),
    );

    expect((await source.load()).total.amount.minorUnits, 4250000);
  });

  test('the wrapper is transparent about what it wraps', () {
    expect(
      RecordingSnapshotSource(
        inner: FixtureSnapshotSource(knownFixture),
        store: store,
      ).isSynthetic,
      isTrue,
      reason: 'wrapping a fixture must not launder it into a real source',
    );
  });
}
