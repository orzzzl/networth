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

/// The curve's series, from a bundled fixture for the same reason the payload is.
///
/// The series is *synthetic*, like every other fixture here — and note what it
/// is not: it is not derived from the payload above. Manufacturing past points
/// out of today's total would invent a history the owner never had, which is the
/// exact deformation §12 forbids, arriving from the demo rather than from a
/// query. The fixture carries a gap and an incomplete reading on purpose, so the
/// two treatments §10.5 asks for are visible rather than only asserted in tests.
const HistorySource _historySource = FixtureHistorySource(
  'assets/fixtures/history.json',
);

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
