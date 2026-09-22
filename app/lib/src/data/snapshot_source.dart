import 'package:flutter/services.dart' show AssetBundle, rootBundle;

import '../debug_log.dart';
import '../domain/phone_payload.dart';
import 'history_store.dart';

/// Where the app gets a payload from.
///
/// The seam exists so the UI can be built and tested against the real payload
/// *shape* without waiting on task 20's HTTP route or task 28. Swapping in the
/// networked implementation later is a change of one constructor at the top of
/// the app, not a change to anything that renders.
///
/// The app holds no Plaid token and never calls Plaid. It reads a published
/// snapshot; that is its whole relationship with the outside world.
abstract interface class SnapshotSource {
  Future<PhonePayload> load();

  /// Whether the payloads this source yields are made up.
  ///
  /// **Declared rather than defaulted, and that is the point.** Task `23a` gives
  /// the app a durable record of the readings it accepts, and a made-up reading
  /// written into it would sit there at the fixture's own `published_at`
  /// forever, indistinguishable from a real point once real ones arrive — the
  /// invented past this project refuses, arriving through the door marked
  /// *record* rather than the one marked *bundle*. [RecordingSnapshotSource]
  /// therefore records nothing from a synthetic source, and because this is an
  /// interface member with no default, a source added later does not compile
  /// until it has said which kind it is.
  bool get isSynthetic;
}

/// Reads a bundled fixture. This is the only implementation in this build.
class FixtureSnapshotSource implements SnapshotSource {
  const FixtureSnapshotSource(this.assetPath, {this.bundle});

  @override
  bool get isSynthetic => true;

  /// Asset key, e.g. `assets/fixtures/mixed_known_and_unknown.json`.
  final String assetPath;

  /// Overridable so a test can supply fixtures without the asset machinery.
  final AssetBundle? bundle;

  @override
  Future<PhonePayload> load() async {
    final source = await (bundle ?? rootBundle).loadString(assetPath);
    return PhonePayload.fromJsonString(source);
  }
}

/// Whether the reading now on screen reached the phone's record.
///
/// **A separate fact from whether the record can be read**, which is why it is
/// its own seam rather than folded into the series the curve receives. History
/// that loads fine and a store that cannot be written is a real state — a full
/// disk is the ordinary way to reach it — and in it the app has a correct
/// headline, a correct curve, and is quietly keeping none of it.
///
/// Read it *after* awaiting [SnapshotSource.load]: the recording happens inside
/// that call, so before it returns this still describes the previous payload.
abstract interface class RecordingStatus {
  /// True when the most recent accepted payload could not be recorded.
  ///
  /// Not latched. It answers for the payload the screen is about to show, so a
  /// launch that records successfully after one that failed reports `false` and
  /// the screen stops warning — the failure being over is as much a fact as the
  /// failure.
  bool get lastRecordingFailed;
}

/// Records every payload it passes through — the accumulating half of task
/// `23a`.
///
/// It wraps the source rather than living in `HomePage` so that "a payload the
/// app accepted" and "a payload the app recorded" are the same event by
/// construction: there is no second call site that could forget, and a screen
/// added later records by using the source like any other.
///
/// **Two failures it must not turn into a third.** A store that cannot be
/// written is not a reason to take the total off the screen — the number with
/// its age is what this product is for — so the write's failure does not
/// propagate. And it is awaited rather than fired and forgotten, so a caller
/// that records then reads sees its own write.
///
/// **But not propagating is not the same as not reporting**, and for a while
/// this class did the second one: the failure went to [debugLog] and nowhere
/// else, so a phone whose store had become unwritable showed the owner either
/// an ordinary curve or the words "no readings recorded yet" — the latter a
/// false claim about his own history, and the same shape as the `historyEmpty`
/// / `historyUnreadable` split the curve already refuses to collapse, one layer
/// over. Every launch could then lose its reading with nothing on screen ever
/// saying so. So the outcome is kept in [lastRecordingFailed] and the screen
/// says which of the three states it is in.
class RecordingSnapshotSource implements SnapshotSource, RecordingStatus {
  RecordingSnapshotSource({required this.inner, required this.store});

  final SnapshotSource inner;
  final HistoryStore store;

  @override
  bool get isSynthetic => inner.isSynthetic;

  @override
  bool get lastRecordingFailed => _lastRecordingFailed;
  bool _lastRecordingFailed = false;

  @override
  Future<PhonePayload> load() async {
    final payload = await inner.load();
    if (inner.isSynthetic) {
      // Not an error and not a throw: this is the shipped wiring until task 22
      // swaps the fixture for the real transport, and the app is expected to
      // keep working — showing a synthetic *today*, which announces itself in
      // `UNKNOWN` and stale annotations, while recording nothing. Passing the
      // payload through unrecorded is the whole refusal.
      //
      // And it is not a *failure* either: nothing was attempted, so reporting
      // one would put a warning about the owner's record on every screen of the
      // build that deliberately keeps no record.
      _lastRecordingFailed = false;
      debugLog(() => 'not recording a payload from a synthetic source');
      return payload;
    }
    try {
      await store.record(payload);
      _lastRecordingFailed = false;
    } on Object catch (error) {
      _lastRecordingFailed = true;
      debugLog(() => 'history not recorded: $error');
    }
    return payload;
  }
}
