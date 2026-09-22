import 'package:flutter/material.dart';

import 'l10n/generated/app_localizations.dart';
import 'src/data/history_source.dart';
import 'src/data/history_store.dart';
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

/// The curve's series — **the phone's own record, and nothing else.**
///
/// The payload above is synthetic and that is fine; a synthetic *past* is not
/// the same object. A made-up today announces itself, because the screen it
/// draws is covered in `UNKNOWN` and stale annotations. A made-up thirty-day
/// curve announces nothing: it carries no figures to recognise as wrong, and it
/// would sit directly under a headline that becomes real before this file is
/// next edited. So no entry point names a bundled series — there is no longer
/// one to name.
///
/// What fills this is [RecordingSnapshotSource] below, one reading per accepted
/// payload. Until task `22` swaps the fixture above for the real transport it
/// records nothing at all, because the recorder refuses a synthetic source, so
/// what the owner sees here is the truth: a new install has recorded nothing,
/// and the curve says so.
HistoryStore _historyStore() => FileHistoryStore.appPrivate();

void main() {
  final history = _historyStore();
  runApp(
    NetWorthApp(
      // **One wrapper, and it is where task 22's change lands.** Recording is a
      // property of accepting a payload rather than of a screen, so it sits on
      // the source: swapping `_source` for the networked one is the whole of
      // making the curve fill, and nothing that renders has to know.
      source: RecordingSnapshotSource(inner: _source, store: history),
      historySource: history,
    ),
  );
}

class NetWorthApp extends StatelessWidget {
  /// Both seams are **required**: there is no default that quietly ships.
  ///
  /// The defaults they replaced meant a test could pump the app with one half of
  /// the production wiring and no way to tell, and — since the history seam is
  /// now a store that writes to a real path — a widget test that forgot to pass
  /// one would have written to the *host's* documents directory.
  const NetWorthApp({
    super.key,
    required this.source,
    required this.historySource,
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
