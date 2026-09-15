import 'package:flutter/services.dart' show AssetBundle, rootBundle;

import '../domain/phone_payload.dart';

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
}

/// Reads a bundled fixture. This is the only implementation in this build.
class FixtureSnapshotSource implements SnapshotSource {
  const FixtureSnapshotSource(this.assetPath, {this.bundle});

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
