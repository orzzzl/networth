import 'package:flutter/material.dart';

import 'l10n/generated/app_localizations.dart';
import 'src/data/fetch_diagnostics_store.dart';
import 'src/data/held_copy_store.dart';
import 'src/data/history_source.dart';
import 'src/data/history_store.dart';
import 'src/data/home_load.dart';
import 'src/data/recording_refresh.dart';
import 'src/data/seq_baseline_store.dart';
import 'src/data/snapshot_reader.dart';
import 'src/data/snapshot_refresh.dart';
import 'src/pairing/pairing_vault.dart';
import 'src/ui/home_page.dart';

/// Where the payload comes from — **the owner's own host, over his tailnet.**
///
/// The app is read-only: it holds no Plaid token and never calls Plaid. Until
/// this commit it read a bundled fixture, which is what let the screen be built
/// and tested against the real payload shape; a shipped build reading a
/// synthetic file is a demo, and every state the task exists for — no pairing,
/// an unreachable host, a copy aging on disk — was unreachable from the app's
/// own entry point.
///
/// Everything below is assembled here rather than in the screen because the
/// screen must not be able to choose its own collaborators: what a test pumps
/// and what the owner runs are then the same object graph, differing only in
/// where the bytes and the keystore come from.
HomeLoader _homeLoader(HistoryStore history) {
  // **One vault instance for both readers.** The two *reads* stay separate and
  // ordered — `HomeLoader` documents why the display scope must be the later
  // one — but there is one Android keystore, and a second vault object would be
  // a second cache of nothing pretending otherwise.
  final vault = PairingVault();

  // **One instance of each store, shared between the refresher and the loader.**
  // `SnapshotRefresher` states the contract it cannot enforce: the three stores
  // must have exactly one writer and it is that class. Building a second
  // `FileHeldCopyStore.appPrivate()` for the read side would put two objects
  // over one directory, which is the shape that contract rules out.
  final heldCopies = FileHeldCopyStore.appPrivate();
  final diagnostics = FileFetchDiagnosticsStore.appPrivate();
  final baselines = FileSeqBaselineStore.appPrivate();

  return HomeLoader(
    vault: vault,
    // **The recorder wraps the refresh, not the screen.** Keeping a reading is
    // a property of accepting a payload rather than of drawing one, so it sits
    // here; nothing that renders has to know, and the screen asks this same
    // object whether the last write succeeded rather than being told separately.
    refresher: RecordingSnapshotRefresher(
      inner: SnapshotRefresher(
        reader: PairedSnapshotReader(vault: vault),
        diagnostics: diagnostics,
        baselines: baselines,
        heldCopies: heldCopies,
        clock: DateTime.now,
      ),
      store: history,
    ),
    heldCopies: heldCopies,
    diagnostics: diagnostics,
    baselines: baselines,
  );
}

/// The curve's series — **the phone's own record, and nothing else.**
///
/// No entry point names a bundled series and there is no longer one to name. A
/// made-up *today* announces itself, because the screen it draws is covered in
/// `UNKNOWN` and stale annotations; a made-up thirty-day curve announces
/// nothing, carrying no figures to recognise as wrong under a headline that is
/// real.
///
/// What fills it is [RecordingSnapshotRefresher] above, one reading per accepted
/// payload. Until this commit that recorded nothing at all, because the source
/// under it was synthetic and the recorder refuses a synthetic source; from here
/// it records what the host actually served, so a new install shows an empty
/// curve that fills as the days pass rather than one that never fills.
HistoryStore _historyStore() => FileHistoryStore.appPrivate();

void main() {
  final history = _historyStore();
  runApp(
    NetWorthApp(
      loader: _homeLoader(history),
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
    required this.loader,
    required this.historySource,
  });

  final HomeLoader loader;
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
      home: HomePage(loader: loader, historySource: historySource),
    );
  }
}
