import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/l10n/generated/app_localizations.dart';
import 'package:networth_app/src/domain/phone_payload.dart';

/// The fixtures the app ships, read from disk rather than through the bundle.
///
/// Reading the real file is the point: these tests are the only thing standing
/// between the fixture seam and a shape the daemon never sends, so they must
/// fail when someone edits the JSON, not when someone edits a copy of it pasted
/// into a test.
const String knownFixture = 'assets/fixtures/known.json';
const String mixedFixture = 'assets/fixtures/mixed_known_and_unknown.json';
const String staticOnlyFixture = 'assets/fixtures/static_only.json';

const List<String> allFixtures = [knownFixture, mixedFixture, staticOnlyFixture];

String readFixture(String assetPath) => File(assetPath).readAsStringSync();

/// Wrap a widget in the localizations it now needs, spelled once.
///
/// Every widget in this app reads its copy from [AppLocalizations], so a bare
/// `MaterialApp(home: ...)` no longer has the ancestors it takes to build one —
/// the delegates have to be installed. Done here rather than in each test file
/// so that adding a locale is one edit, and so a test cannot quietly pump a
/// subtree with a different set of delegates from the one the app ships.
///
/// [scaffold] is false for widgets that bring their own — `HomePage` does — so
/// that pumping one does not nest two.
Widget localized(Widget child, {bool scaffold = true}) => MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: scaffold ? Scaffold(body: child) : child,
    );

PhonePayload loadFixture(String assetPath) =>
    PhonePayload.fromJsonString(readFixture(assetPath));

/// An [AssetBundle] backed by strings, so the seam can be exercised without the
/// asset machinery a widget test would otherwise need.
class StringAssetBundle extends CachingAssetBundle {
  StringAssetBundle(this.contents);

  /// Every shipped fixture, keyed by the asset path the app uses.
  factory StringAssetBundle.ofFixtures() => StringAssetBundle({
        for (final path in allFixtures) path: readFixture(path),
      });

  final Map<String, String> contents;

  @override
  Future<ByteData> load(String key) async {
    final text = contents[key];
    if (text == null) {
      throw FlutterError('no fixture registered for "$key"');
    }
    return ByteData.sublistView(Uint8List.fromList(utf8.encode(text)));
  }
}

/// Every string rendered by a [Text] in the subtree, in tree order.
///
/// Used to assert the *absence* of a date, which is a claim about the whole
/// rendered surface and cannot be made with `find.text` against a string the
/// test would have to guess at.
List<String> renderedText(WidgetTester tester, Finder root) {
  return tester
      .widgetList<Text>(find.descendant(of: root, matching: find.byType(Text)))
      .map((text) => text.data ?? '')
      .where((value) => value.isNotEmpty)
      .toList();
}
