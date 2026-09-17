import 'dart:convert';

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
/// so that is visible rather than pretended. Accumulating needs task 20's real
/// transport; keeping needs durable storage, which needs a package, and
/// `AGENTS.md` admits no dependency without an OK written in the task spec —
/// task 23's carries none. A `record()` method here would be the same dead
/// weight `phone_payload.dart` refuses when it declines to parse `accounts`: an
/// API that looks like support for a thing this build cannot do.
///
/// So this mirrors `SnapshotSource` exactly, for the same reason it exists: the
/// curve is built and tested against the real *shape* now, and the
/// implementation that accumulates is a change of one constructor at the top of
/// the app rather than a change to anything that renders.
abstract interface class HistorySource {
  Future<NetWorthHistory> load();
}

/// Reads a bundled fixture. This is the only implementation in this build.
class FixtureHistorySource implements HistorySource {
  const FixtureHistorySource(this.assetPath, {this.bundle});

  /// Asset key, e.g. `assets/fixtures/history.json`.
  final String assetPath;

  /// Overridable so a test can supply a series without the asset machinery.
  final AssetBundle? bundle;

  @override
  Future<NetWorthHistory> load() async {
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
