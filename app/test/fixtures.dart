import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/l10n/generated/app_localizations.dart';
import 'package:networth_app/src/data/history_source.dart';
import 'package:networth_app/src/data/history_store.dart';
import 'package:networth_app/src/domain/clock_continuity.dart';
import 'package:networth_app/src/domain/net_worth_history.dart';
import 'package:networth_app/src/domain/phone_payload.dart';

/// A clock this device has proved continuous: the same interval measured two
/// ways, agreeing exactly. The neutral value for tests whose subject is *not*
/// whether the clock can be trusted — age, placement, or whether a block is
/// drawn at all.
///
/// Shared from here for the same reason [localized] is: `continuity` is a
/// required parameter precisely so that no caller can quietly assert the
/// reassuring answer, and a per-file copy of the value that means "assume it is
/// fine" is how that requirement decays into a default with extra steps.
const ClockContinuity trustedClock =
    ContinuityHeld(wallElapsed: Duration.zero, monotonicElapsed: Duration.zero);

/// The fixtures the app ships, read from disk rather than through the bundle.
///
/// Reading the real file is the point: these tests are the only thing standing
/// between the fixture seam and a shape the daemon never sends, so they must
/// fail when someone edits the JSON, not when someone edits a copy of it pasted
/// into a test.
const String knownFixture = 'assets/fixtures/known.json';
const String mixedFixture = 'assets/fixtures/mixed_known_and_unknown.json';
const String staticOnlyFixture = 'assets/fixtures/static_only.json';

/// A payload with a non-empty §11 alert set — **added rather than folded into
/// one of the three above**, which all carry `alerts: []` and are unchanged.
///
/// Editing one of them would have been the cheaper diff and the worse one: those
/// three are what every existing test asserts against, and the empty set is
/// itself a case worth keeping (it is the ordinary one, and the screen must show
/// no alert block at all for it).
///
/// It is internally consistent the way the host would have produced it, because
/// a fixture that could not have come off `publisher.py` tests the parser
/// against a shape production never sends: two accounts on re-auth Items are
/// `STALE` and carried forward with `reauth_account_count: 2`, the unreconciled
/// third has no observation and no freshness and is excluded from `value_minor`,
/// and `is_complete` is `false` because §10.5 makes it false whenever anything
/// was carried forward or unreconciled.
///
/// Its `pairing_id` is a UUIDv4 rather than the three siblings' literal
/// `fixture-pairing`, which is PR #101's finding applied one layer down:
/// `PairingProvision.parse` requires a UUIDv4, so a fixture carrying anything
/// else is one no real pairing can ever be holding — fine while nothing checks,
/// and a happy path built on a configuration production cannot reach the moment
/// a later test lifts it into an envelope.
const String alertsOpenFixture = 'assets/fixtures/alerts_open.json';

const List<String> allFixtures = [
  knownFixture,
  mixedFixture,
  staticOnlyFixture,
  alertsOpenFixture,
];

String readFixture(String assetPath) => File(assetPath).readAsStringSync();

/// A series carrying both treatments §10.5 asks for — a gap, and one incomplete
/// reading — **in the test, because the app no longer ships one.**
///
/// This was `assets/fixtures/history.json` until task `23a` deleted it along
/// with the demo entry point that rendered it. The rule those two failed is the
/// one the row is named after: a made-up *past* announces nothing, so none may
/// be reachable from a build. A test is not a build.
///
/// It is the real shape rather than a convenient one — `published_at`, `seq`
/// and a `total` as the host sends it, which is exactly what `FileHistoryStore`
/// writes. `history_store_test.dart` is what pins that claim: it records
/// payloads, reads the file back through the same [parseHistory] used here, and
/// would go red if the store's format and this one drifted apart.
const String demoSeriesJson = '''
[
  {"published_at": "2026-09-05T04:00:00.000000Z", "seq": "1", "total": {
    "age_state": "KNOWN", "as_of": "2026-09-05T01:35:00.000000Z", "currency": "USD",
    "value_minor": 4812000, "assets_minor": 4812000, "liabilities_minor": 0,
    "static_account_count": 0, "is_complete": true}},
  {"published_at": "2026-09-06T04:00:00.000000Z", "seq": "2", "total": {
    "age_state": "KNOWN", "as_of": "2026-09-06T01:35:00.000000Z", "currency": "USD",
    "value_minor": 4835500, "assets_minor": 4835500, "liabilities_minor": 0,
    "static_account_count": 0, "is_complete": true}},
  {"published_at": "2026-09-07T04:00:00.000000Z", "seq": "3", "total": {
    "age_state": "KNOWN", "as_of": "2026-09-07T01:35:00.000000Z", "currency": "USD",
    "value_minor": 4801200, "assets_minor": 4801200, "liabilities_minor": 0,
    "static_account_count": 0, "is_complete": true}},
  {"published_at": "2026-09-10T04:00:00.000000Z", "seq": "4", "total": {
    "age_state": "KNOWN", "as_of": "2026-09-10T01:35:00.000000Z", "currency": "USD",
    "value_minor": 4776900, "assets_minor": 4776900, "liabilities_minor": 0,
    "static_account_count": 0, "is_complete": true}},
  {"published_at": "2026-09-11T04:00:00.000000Z", "seq": "5", "total": {
    "age_state": "KNOWN", "as_of": "2026-09-11T01:35:00.000000Z", "currency": "USD",
    "value_minor": 4780300, "assets_minor": 4780300, "liabilities_minor": 0,
    "static_account_count": 0, "is_complete": false}},
  {"published_at": "2026-09-12T04:00:00.000000Z", "seq": "6", "total": {
    "age_state": "KNOWN", "as_of": "2026-09-12T01:35:00.000000Z", "currency": "USD",
    "value_minor": 4790000, "assets_minor": 4790000, "liabilities_minor": 0,
    "static_account_count": 0, "is_complete": true}},
  {"published_at": "2026-09-12T19:30:00.000000Z", "seq": "7", "total": {
    "age_state": "KNOWN", "as_of": "2026-09-12T18:05:00.000000Z", "currency": "USD",
    "value_minor": 4844100, "assets_minor": 4844100, "liabilities_minor": 0,
    "static_account_count": 0, "is_complete": true}},
  {"published_at": "2026-09-13T04:00:00.000000Z", "seq": "8", "total": {
    "age_state": "KNOWN", "as_of": "2026-09-13T01:35:00.000000Z", "currency": "USD",
    "value_minor": 4861700, "assets_minor": 4861700, "liabilities_minor": 0,
    "static_account_count": 0, "is_complete": true}},
  {"published_at": "2026-09-14T04:00:00.000000Z", "seq": "9", "total": {
    "age_state": "KNOWN", "as_of": "2026-09-14T01:35:00.000000Z", "currency": "USD",
    "value_minor": 4903400, "assets_minor": 4903400, "liabilities_minor": 0,
    "static_account_count": 0, "is_complete": true}},
  {"published_at": "2026-09-15T04:00:00.000000Z", "seq": "10", "total": {
    "age_state": "KNOWN", "as_of": "2026-09-15T01:35:00.000000Z", "currency": "USD",
    "value_minor": 4925000, "assets_minor": 4925000, "liabilities_minor": 0,
    "static_account_count": 0, "is_complete": true}}
]
''';

/// A store on [directory], using the shipped class rather than a stand-in.
///
/// A test that wants "the phone has recorded nothing" points one at a fresh
/// temporary directory: that is the real first-launch state, and it exercises
/// the code the app ships instead of a twin of it.
FileHistoryStore storeIn(Directory directory) => FileHistoryStore(
      open: () async => File('${directory.path}/${FileHistoryStore.fileName}'),
    );

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

NetWorthHistory demoSeries() => parseHistory(demoSeriesJson);

// Tests whose subject is the payload half of the screen hand `HomePage` the
// production `EmptyHistorySource` from `src/data/history_source.dart`. There
// used to be a private copy here; it was deleted when that class became the
// app's own production wiring, because a test-local twin of a shipped class is
// how a test comes to pass against code nobody ships.

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
