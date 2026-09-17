import 'package:flutter/material.dart';

import 'l10n/generated/app_localizations.dart';
import 'src/data/history_source.dart';
import 'src/data/snapshot_source.dart';
import 'src/ui/home_page.dart';

/// Which published snapshot this build shows.
///
/// The app is read-only: it holds no Plaid token and never calls Plaid. Today
/// the payload comes from a bundled fixture so the screen can be built and
/// tested against the real payload shape without waiting on task 20's HTTP
/// route. The mixed known/unknown fixture is the default deliberately — it is
/// the state the display is most easily got wrong in, so it is the one visible
/// by default rather than the flattering one.
const SnapshotSource _source = FixtureSnapshotSource(
  'assets/fixtures/mixed_known_and_unknown.json',
);

/// The curve's series — **empty, and deliberately not a fixture.**
///
/// The payload above is synthetic and that is fine; a synthetic *past* is not
/// the same object. A made-up today announces itself, because the screen it
/// draws is covered in `UNKNOWN` and stale annotations. A made-up thirty-day
/// curve announces nothing: it carries no figures to recognise as wrong, and it
/// would sit directly under a headline that becomes real before this file is
/// next edited. So this entry point does not name the fixture at all — the
/// synthetic series is reachable only from `main_demo.dart`, and
/// `FixtureHistorySource` refuses to load in a release build besides.
///
/// What the owner sees here is therefore the truth: a new install has recorded
/// nothing, and the curve says so. Task `23a` is what fills it.
const HistorySource _historySource = EmptyHistorySource();

void main() {
  runApp(const NetWorthApp());
}

class NetWorthApp extends StatelessWidget {
  const NetWorthApp({
    super.key,
    this.source = _source,
    this.historySource = _historySource,
  });

  final SnapshotSource source;
  final HistorySource historySource;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      // The one string that cannot come from `AppLocalizations`: the OS reads it
      // before a locale is resolved, so there is no context to look one up in.
      // `onGenerateTitle` is the seam for that, and it runs with a context.
      onGenerateTitle: (context) => AppLocalizations.of(context).appTitle,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF2F6F4E)),
        useMaterial3: true,
      ),
      home: HomePage(source: source, historySource: historySource),
    );
  }
}
