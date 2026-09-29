import 'dart:async';
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

/// Inner attempts serialised exactly as [SnapshotRefresher] serialises its own,
/// because that queue's *end* is what the ordering finding is about: it finishes
/// when `refresh()` returns, leaving the decorator's history write outside it.
class _SerialInner implements SnapshotRefreshing {
  Future<void> _queue = Future<void>.value();
  int calls = 0;

  @override
  Future<SnapshotRefreshOutcome> refresh() {
    final next = _queue.then((_) {
      calls += 1;
      return RefreshAccepted(
        payload(publishedAt: '2026-09-15T04:00:00Z', seq: '$calls', valueMinor: 4250000 + calls),
      );
    });
    _queue = next.then((_) {});
    return next;
  }
}

/// A store whose **first** write parks until released, so an overlap can be built
/// deterministically rather than raced for.
class _GatedStore implements HistoryStore {
  _GatedStore(this.inner, {required this.firstWriteFails});

  final HistoryStore inner;
  final bool firstWriteFails;
  final entered = Completer<void>();
  final release = Completer<void>();
  int calls = 0;

  @override
  Future<NetWorthHistory> load() => inner.load();

  @override
  Future<void> record(PhonePayload value) async {
    calls += 1;
    if (calls == 1) {
      entered.complete();
      await release.future;
      if (firstWriteFails) {
        throw const FileSystemException('synthetic first write failure');
      }
    } else if (!firstWriteFails) {
      throw const FileSystemException('synthetic later write failure');
    }
    return inner.record(value);
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

  group('overlapping calls are serialised through the history write', () {
    // Review's finding, and the reason it is not caught by anything above: the
    // inner queue ends when `inner.refresh()` returns, so before this the record
    // and the status update ran outside it. Two calls could accept A then B,
    // finish B's write, then finish A's — leaving a flag describing A while B is
    // the copy on screen.
    // Named apart from the outer `directory`/`store` rather than shadowing them:
    // the first draft of this group shadowed `directory`, so one test wrote through
    // the group's store and asserted on the outer one. It read as a real failure.
    late Directory orderDirectory;
    late FileHistoryStore orderStore;

    setUp(() {
      orderDirectory = Directory.systemTemp.createTempSync('networth-refresh-order');
      orderStore = storeIn(orderDirectory);
    });

    tearDown(() => orderDirectory.deleteSync(recursive: true));

    test('a queued refresh cannot overtake the one whose write is in flight', () async {
      // **Stated positively, on purpose.** Asserting only the final flag would
      // also pass on an implementation that happened to interleave harmlessly on
      // this schedule; what the fix actually establishes is that the second
      // attempt has not begun, and that is observable while the first is parked.
      final store = _GatedStore(orderStore, firstWriteFails: false);
      final inner = _SerialInner();
      final refresher = RecordingSnapshotRefresher(inner: inner, store: store);

      final first = refresher.refresh();
      await store.entered.future;
      final second = refresher.refresh();
      // Every chance to run, rather than a timer: if the operation were not
      // serialised, this is where the second attempt would get in.
      await pumpEventQueue();

      expect(inner.calls, 1, reason: 'the second refresh attempted while the first was writing');
      expect(store.calls, 1, reason: 'a second history write overtook the first');

      store.release.complete();
      await first;
      await second;

      expect(inner.calls, 2);
      expect(store.calls, 2);
    });

    test('the flag describes the later accepted payload, not the later write', () async {
      // A's write fails, B's succeeds. Serialised, A finishes first and B last, so
      // the flag ends up `false` — and it is `false` *about B*, which is the copy
      // the screen is showing. Before the fix an older write could land last and
      // answer for a payload that is no longer on screen.
      final store = _GatedStore(orderStore, firstWriteFails: true);
      final refresher = RecordingSnapshotRefresher(inner: _SerialInner(), store: store);

      final first = refresher.refresh();
      await store.entered.future;
      final second = refresher.refresh();
      // **Load-bearing, and it was missing from the first draft.** Without it the
      // second call has not reached its own write when the gate opens, so the bad
      // interleaving never happens and the test passes with or without the fix —
      // measured: it stayed green against the unserialised version. Pumping here
      // gives the overtake every chance to occur, which is what makes the final
      // assertion evidence.
      await pumpEventQueue();
      store.release.complete();
      await first;
      await second;

      expect(refresher.lastRecordingFailed, isFalse,
          reason: 'the newest accepted payload did reach the record');
      expect((await orderStore.load()).points, hasLength(1));
    });

    test('and the mirror: a later failed write is still the one reported', () async {
      // A's write succeeds, B's fails. The flag must end up `true`: B is the
      // latest accepted payload and it is missing from the record. This is the
      // direction that fails if the *older* completion is allowed to win.
      final store = _GatedStore(orderStore, firstWriteFails: false);
      final refresher = RecordingSnapshotRefresher(inner: _SerialInner(), store: store);

      final first = refresher.refresh();
      await store.entered.future;
      final second = refresher.refresh();
      // **Load-bearing, and it was missing from the first draft.** Without it the
      // second call has not reached its own write when the gate opens, so the bad
      // interleaving never happens and the test passes with or without the fix —
      // measured: it stayed green against the unserialised version. Pumping here
      // gives the overtake every chance to occur, which is what makes the final
      // assertion evidence.
      await pumpEventQueue();
      store.release.complete();
      await first;
      await second;

      expect(refresher.lastRecordingFailed, isTrue,
          reason: 'the newest accepted payload never reached the record');
    });

    test('a failed write does not poison the queue for the next refresh', () async {
      // The `onError` swallow in `refresh()`. Without it one defect turns into a
      // decorator that never records again — the same reason `SnapshotRefresher`
      // guards its own chain.
      final failing = _SwitchableStore(orderStore);
      final stub = _StubRefresh(RefreshAccepted(accepted()));
      final refresher = RecordingSnapshotRefresher(inner: stub, store: failing);

      failing.broken = true;
      await refresher.refresh();
      failing.broken = false;
      stub.outcome = RefreshAccepted(accepted(seq: '2', valueMinor: 4260000));
      await refresher.refresh();

      expect(refresher.lastRecordingFailed, isFalse);
      expect((await orderStore.load()).points, hasLength(1));
    });
  });
}
