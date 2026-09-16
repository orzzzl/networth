import 'package:flutter/material.dart';

import 'l10n/generated/app_localizations.dart';
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

void main() {
  runApp(const NetWorthApp());
}

class NetWorthApp extends StatelessWidget {
  const NetWorthApp({super.key, this.source = _source});

  final SnapshotSource source;

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
      home: HomePage(source: source),
    );
  }
}
