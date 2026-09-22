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

/// A store whose writes can be broken and then repaired, so the *recovery* can
/// be measured and not only the failure.
class _SwitchableStore implements HistoryStore {
  _SwitchableStore(this.inner);

  final HistoryStore inner;
  bool broken = false;

  @override
  Future<NetWorthHistory> load() => inner.load();

  @override
  Future<void> record(PhonePayload payload) async {
    if (broken) {
      throw const FileSystemException('no space left on device');
    }
    return inner.record(payload);
  }
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

  group('a store that will not write', () {
    test('does not take the total off the screen', () async {
      // The number with its age is what this product is for; the curve is what
      // it adds. Same rule `HomePage` applies to an unreadable series, one layer
      // down, because the write happens before anything is rendered.
      final source = RecordingSnapshotSource(
        inner: _RealSource(payload(publishedAt: '2026-09-15T04:00:00Z')),
        store: _BrokenStore(),
      );

      expect((await source.load()).total.amount.minorUnits, 4250000);
    });

    test('but does say so, rather than only to the debug log', () async {
      // **Not propagating is not the same as not reporting**, and this class did
      // the second one until review reproduced the cost: a phone whose store had
      // become unwritable lost every reading with nothing on screen ever saying
      // so. `lastRecordingFailed` is what the screen reads.
      final source = RecordingSnapshotSource(
        inner: _RealSource(payload(publishedAt: '2026-09-15T04:00:00Z')),
        store: _BrokenStore(),
      );

      expect(source.lastRecordingFailed, isFalse,
          reason: 'nothing has been attempted yet');
      await source.load();
      expect(source.lastRecordingFailed, isTrue);
    });

    test('and stops saying so once a write succeeds', () async {
      // Not latched. The failure being over is as much a fact as the failure,
      // and a warning that never clears is one the owner learns to read past.
      final failing = _SwitchableStore(store);
      final source = RecordingSnapshotSource(
        inner: _RealSource(payload(publishedAt: '2026-09-15T04:00:00Z')),
        store: failing,
      );

      failing.broken = true;
      await source.load();
      expect(source.lastRecordingFailed, isTrue);

      failing.broken = false;
      await source.load();

      expect(source.lastRecordingFailed, isFalse);
      expect((await store.load()).points, hasLength(1),
          reason: 'the recovered launch recorded its reading for real');
    });

    test('a synthetic source reports no failure, because it attempted none',
        () async {
      // The shipped wiring records nothing on purpose. Reporting that as a
      // failure would put a warning about the owner's record on every screen of
      // the build that deliberately keeps no record.
      final source = RecordingSnapshotSource(
        inner: FixtureSnapshotSource(knownFixture, bundle: StringAssetBundle.ofFixtures()),
        store: _BrokenStore(),
      );

      await source.load();

      expect(source.lastRecordingFailed, isFalse);
    });
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
