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
/// its age is what this product is for — so the write's failure is logged and
/// swallowed, exactly as `HomePage` treats an unreadable series. And it is
/// awaited rather than fired and forgotten, so a caller that records then reads
/// sees its own write.
class RecordingSnapshotSource implements SnapshotSource {
  const RecordingSnapshotSource({required this.inner, required this.store});

  final SnapshotSource inner;
  final HistoryStore store;

  @override
  bool get isSynthetic => inner.isSynthetic;

  @override
  Future<PhonePayload> load() async {
    final payload = await inner.load();
    if (inner.isSynthetic) {
      // Not an error and not a throw: this is the shipped wiring until task 22
      // swaps the fixture for the real transport, and the app is expected to
      // keep working — showing a synthetic *today*, which announces itself in
      // `UNKNOWN` and stale annotations, while recording nothing. Passing the
      // payload through unrecorded is the whole refusal.
      debugLog(() => 'not recording a payload from a synthetic source');
      return payload;
    }
    try {
      await store.record(payload);
    } on Object catch (error) {
      debugLog(() => 'history not recorded: $error');
    }
    return payload;
  }
}
