import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/history_store.dart';
import 'package:networth_app/src/data/recording_refresh.dart';
import 'package:networth_app/src/data/snapshot_refresh.dart';
import 'package:networth_app/src/domain/net_worth_history.dart';
import 'package:networth_app/src/domain/phone_payload.dart';

import 'fixtures.dart';
import 'history_store_test.dart' show payload;

/// A refresh whose outcome the test chooses, and which counts its calls.
class _StubRefresh implements SnapshotRefreshing {
  _StubRefresh(this.outcome);

  SnapshotRefreshOutcome outcome;
  int calls = 0;

  @override
  Future<SnapshotRefreshOutcome> refresh() async {
    calls += 1;
    return outcome;
  }
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

  PhonePayload accepted({int valueMinor = 4250000, String seq = '1'}) =>
      payload(publishedAt: '2026-09-15T04:00:00Z', seq: seq, valueMinor: valueMinor);

  setUp(() {
    directory = Directory.systemTemp.createTempSync('networth-recording-refresh');
    store = storeIn(directory);
  });

  tearDown(() => directory.deleteSync(recursive: true));

  test('a payload the phone keeps is a payload the phone records', () async {
    final refresher = RecordingSnapshotRefresher(
      inner: _StubRefresh(RefreshAccepted(accepted())),
      store: store,
    );

    final outcome = await refresher.refresh();

    expect(outcome, isA<RefreshAccepted>());
    // Awaited rather than fired and forgotten: a caller that refreshes then
    // reads sees its own write, which is what lets one screen show the point it
    // just fetched.
    expect((await store.load()).points.single.total.amount.minorUnits, 4250000);
  });

  test('the outcome is passed through unchanged', () async {
    // The decorator owes its caller exactly what it wrapped. `HomeLoader`
    // ignores the outcome, but the next caller need not, and a wrapper that
    // substituted its own would be a second source for the same fact.
    final inner = RefreshAccepted(accepted());
    final refresher = RecordingSnapshotRefresher(inner: _StubRefresh(inner), store: store);

    expect(identical(await refresher.refresh(), inner), isTrue);
  });

  group('an outcome that kept no new payload records nothing', () {
    // The hazard this class exists to avoid: a reading filed on every launch
    // would draw a flat run of identical points, which reads as "the net worth
    // held steady" when what happened is that nobody looked.
    test('a refused refresh leaves the series alone', () async {
      final refresher = RecordingSnapshotRefresher(
        inner: _StubRefresh(const RefreshKeptHeldCopy(RefreshRefusal.attemptFailed)),
        store: store,
      );

      await refresher.refresh();

      expect((await store.load()).isEmpty, isTrue);
      expect(
        File('${directory.path}/${FileHistoryStore.fileName}').existsSync(),
        isFalse,
      );
    });

    test('an unpaired phone leaves the series alone', () async {
      final refresher = RecordingSnapshotRefresher(
        inner: _StubRefresh(const RefreshNotAttempted(NoPairing.notPaired)),
        store: store,
      );

      await refresher.refresh();

      expect((await store.load()).isEmpty, isTrue);
    });

    test('and the control: the same wrapper does record an accepted one', () async {
      // Without this, "the store is empty" would also be satisfied by a wrapper
      // that records nothing at all, and the refusal would be indistinguishable
      // from a broken recorder.
      final refresher = RecordingSnapshotRefresher(
        inner: _StubRefresh(RefreshAccepted(accepted())),
        store: store,
      );

      await refresher.refresh();

      expect((await store.load()).points, hasLength(1));
    });

    test('one accepted payload is one point, however often it is refreshed',
        () async {
      // `HeldCopyStore` already holds the copy; re-recording the same accepted
      // payload on a later launch would file the same reading twice under two
      // different arrival times.
      final stub = _StubRefresh(RefreshAccepted(accepted()));
      final refresher = RecordingSnapshotRefresher(inner: stub, store: store);

      await refresher.refresh();
      stub.outcome = const RefreshKeptHeldCopy(RefreshRefusal.attemptFailed);
      await refresher.refresh();
      await refresher.refresh();

      expect(stub.calls, 3, reason: 'every refresh still reached the inner refresher');
      expect((await store.load()).points, hasLength(1));
    });
  });

  group('a store that will not write', () {
    test('does not take the total off the screen', () async {
      // The number with its age is what this product is for; the curve is what
      // it adds. A failed record must still return the outcome that carries the
      // payload the screen is about to draw.
      final refresher = RecordingSnapshotRefresher(
        inner: _StubRefresh(RefreshAccepted(accepted())),
        store: _BrokenStore(),
      );

      final outcome = await refresher.refresh();

      expect(outcome, isA<RefreshAccepted>());
      expect((outcome as RefreshAccepted).payload.total.amount.minorUnits, 4250000);
    });

    test('but does say so, rather than only to the debug log', () async {
      final refresher = RecordingSnapshotRefresher(
        inner: _StubRefresh(RefreshAccepted(accepted())),
        store: _BrokenStore(),
      );

      expect(refresher.lastRecordingFailed, isFalse,
          reason: 'nothing has been attempted yet');
      await refresher.refresh();
      expect(refresher.lastRecordingFailed, isTrue);
    });

    test('and stops saying so once a write succeeds', () async {
      // Not latched. The failure being over is as much a fact as the failure,
      // and a warning that never clears is one the owner learns to read past.
      final failing = _SwitchableStore(store);
      final stub = _StubRefresh(RefreshAccepted(accepted()));
      final refresher = RecordingSnapshotRefresher(inner: stub, store: failing);

      failing.broken = true;
      await refresher.refresh();
      expect(refresher.lastRecordingFailed, isTrue);

      failing.broken = false;
      stub.outcome = RefreshAccepted(accepted(seq: '2', valueMinor: 4260000));
      await refresher.refresh();

      expect(refresher.lastRecordingFailed, isFalse);
      expect((await store.load()).points, hasLength(1),
          reason: 'the recovered refresh recorded its reading for real');
    });

    test('a refusal after a failed write keeps saying so', () async {
      // **The difference from `RecordingSnapshotSource`, and the reason this
      // class does not simply clear the flag at the top of every call.** That
      // one answers about the payload it just loaded, so every load re-measures
      // it. Here a refusal accepts nothing, and the copy already on screen stays
      // on screen — the very copy whose reading failed to reach the record.
      // Clearing would announce that reading as recorded on the strength of a
      // write that never happened.
      final stub = _StubRefresh(RefreshAccepted(accepted()));
      final refresher = RecordingSnapshotRefresher(inner: stub, store: _BrokenStore());

      await refresher.refresh();
      expect(refresher.lastRecordingFailed, isTrue);

      stub.outcome = const RefreshKeptHeldCopy(RefreshRefusal.attemptFailed);
      await refresher.refresh();

      expect(refresher.lastRecordingFailed, isTrue,
          reason: 'the copy on screen is unchanged, so the fact about it is unchanged');
    });

    test('a refusal before any attempt reports no failure', () async {
      // The mirror of the case above, and the reason the flag starts `false`:
      // a launch that never accepted anything has no failed write to report,
      // and claiming one would warn about a record nothing has touched.
      final refresher = RecordingSnapshotRefresher(
        inner: _StubRefresh(const RefreshNotAttempted(NoPairing.pairingUnreadable)),
        store: _BrokenStore(),
      );

      await refresher.refresh();

      expect(refresher.lastRecordingFailed, isFalse);
    });
  });
}
