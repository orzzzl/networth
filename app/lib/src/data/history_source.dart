import 'dart:convert';

import 'package:flutter/foundation.dart' show kReleaseMode;
import 'package:flutter/services.dart' show AssetBundle, rootBundle;

import '../domain/net_worth_history.dart';
import '../domain/payload_format_exception.dart';

/// Where the curve's points come from.
///
/// **The payload carries one total, not a series.** `publisher.py::_plaintext`
/// sends a single `total`, and `DESIGN.md` §6.2 states the other half where it
/// describes what a compromised phone leaks — *"the phone's local cache … does
/// contain the history window the curve renders, because the curve has to come
/// from somewhere."* So the series is the phone's, accumulated one reading per
/// fetched payload and kept across launches.
///
/// **Neither of those two verbs exists in this build**, and this seam is shaped
/// so that is visible rather than pretended. Accumulating needs the app's real
/// transport; keeping needs durable storage, which needs a package, and
/// `AGENTS.md` admits no dependency without an OK written in the task spec —
/// task 23's carries none. A `record()` method here would be the same dead
/// weight `phone_payload.dart` refuses when it declines to parse `accounts`: an
/// API that looks like support for a thing this build cannot do.
///
/// **Task `23a` owns both verbs, and task 24 (the APK) depends on it**, so the
/// missing half cannot reach the owner as a silent gap. Until then production
/// wires [EmptyHistorySource] and the synthetic series is reachable only from
/// `main_demo.dart` in a debug build.
///
/// So this mirrors `SnapshotSource` exactly, for the same reason it exists: the
/// curve is built and tested against the real *shape* now, and the
/// implementation that accumulates is a change of one constructor at the top of
/// the app rather than a change to anything that renders.
abstract interface class HistorySource {
  Future<NetWorthHistory> load();
}

/// What the app has before anything has recorded a reading: nothing.
///
/// **This is the production wiring** (`main.dart`), and it is not a placeholder
/// standing in for the fixture. The phone genuinely has no stored history until
/// the task named in `tasks/README.md` records one, and the honest rendering of
/// that is `HistoryCurve`'s "no readings recorded yet" — the same discipline as
/// `UNKNOWN` never displaying as fresh. An empty curve is a true statement about
/// a new install and will stay reachable forever, on every first launch.
class EmptyHistorySource implements HistorySource {
  const EmptyHistorySource();

  @override
  Future<NetWorthHistory> load() async => NetWorthHistory.empty;
}

/// Reads a bundled fixture. **Demo and tests only — it refuses to run in a
/// release build.**
///
/// The payload fixture next door is a different kind of object and the asymmetry
/// is the whole point. A synthetic *today* announces itself: the screen it draws
/// is covered in `UNKNOWN` and stale badges, so nobody mistakes it for the
/// owner's money. A synthetic *past* announces nothing — thirty invented points
/// look exactly like thirty real ones, the curve carries no figures that could
/// be recognised as wrong, and it would sit directly beneath a headline that is
/// eventually real. That is this project's own failure mode, rendered as a
/// picture, so the refusal is structural rather than a comment asking the next
/// author to remember.
///
/// It refuses in [load] rather than in the constructor because the constructor
/// is `const`, and by `throw` rather than `assert` because asserts are stripped
/// from exactly the build this is guarding. A release build that reaches here
/// shows "couldn't read the history" and keeps its headline — never fiction.
class FixtureHistorySource implements HistorySource {
  const FixtureHistorySource(this.assetPath, {this.bundle, this.isReleaseBuild = kReleaseMode});

  /// Asset key, e.g. `assets/fixtures/history.json`.
  final String assetPath;

  /// Overridable so a test can supply a series without the asset machinery.
  final AssetBundle? bundle;

  /// Injected so the refusal itself is testable. `flutter test` runs in debug,
  /// so a guard keyed only on the real [kReleaseMode] could never be executed by
  /// a test — it would be a guard whose passing state is that it never ran.
  final bool isReleaseBuild;

  @override
  Future<NetWorthHistory> load() async {
    if (isReleaseBuild) {
      throw StateError(
        'refusing to render the synthetic history fixture in a release build: '
        'a made-up curve under a real headline is the thing this app exists to '
        'refuse (see tasks/README.md, task 23a)',
      );
    }
    final source = await (bundle ?? rootBundle).loadString(assetPath);
    return parseHistory(source);
  }
}

/// Parse a series of readings into the curve's points.
///
/// Refuses rather than guesses, the same way `DatedTotal.fromJson` does — each
/// reading's `total` goes through that very parser, so a series cannot hold a
/// total the headline would have rejected.
///
/// Reduction to one point per day happens here and nowhere else
/// ([NetWorthHistory.reduce]), which is the rule `DESIGN.md` §7 states rather
/// than leaves to a renderer.
NetWorthHistory parseHistory(String source) {
  final Object? decoded;
  try {
    decoded = jsonDecode(source);
  } on FormatException catch (error) {
    throw PayloadFormatException('history is not JSON: ${error.message}');
  }
  if (decoded is! List<Object?>) {
    throw const PayloadFormatException('history is not a JSON array');
  }
  return NetWorthHistory.reduce([
    for (final reading in decoded) HistoryPoint.fromJson(_object(reading)),
  ]);
}

Map<String, Object?> _object(Object? reading) {
  if (reading is! Map<String, Object?>) {
    throw const PayloadFormatException('history reading is not a JSON object');
  }
  return reading;
}
