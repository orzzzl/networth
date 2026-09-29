import 'package:flutter/material.dart';

import '../../l10n/generated/app_localizations.dart';
import '../data/history_source.dart';
import '../data/home_load.dart';
import '../data/snapshot_source.dart';
import '../debug_log.dart';
import '../domain/clock_continuity.dart';
import '../domain/held_copy.dart';
import '../domain/net_worth_history.dart';
import '../domain/phone_payload.dart';
import 'snapshot_view.dart';

/// The one screen this build has.
///
/// Note what the loading and failure branches render: not a total. The headline
/// cannot be built without a [PhonePayload], and a payload cannot be built
/// without an age state, so "the number appears for a moment before its
/// annotation catches up" is not a state this screen can reach — which is what
/// the acceptance criterion about intermediate states is asking for.
///
/// **What it is built over is now [HomeLoad], and that is this commit.** Until
/// here the screen read a [SnapshotSource]: one call, one payload or a throw.
/// A phone has more answers than that — it can be in no pairing, unable to read
/// the pairing it is in, or holding a copy it cannot use — and every one of them
/// arrived as the same error screen. The seven branches below are the states
/// `home_load.dart` and `held_copy.dart` already distinguish, rendered as the
/// distinct sentences they are.
class HomePage extends StatefulWidget {
  const HomePage({
    super.key,
    required this.loader,
    required this.historySource,
    this.clock,
    this.continuity,
  });

  /// Refresh, then read — and, when its refresher is a [RecordingStatus], the
  /// answer to whether the payload on screen was kept.
  ///
  /// **Asked of the loader's own refresher rather than wired separately, which
  /// is the property this had to keep across the seam change.** The recording
  /// outcome used to arrive as its own optional constructor argument, so a
  /// caller that wired the recorder and forgot the second seam got the
  /// reassuring answer by default — a build that loses every reading, silently.
  /// One object cannot be half-wired, and the object that records is the
  /// refresher, so that is the object asked.
  final HomeLoader loader;

  final HistorySource historySource;

  /// Overridable so a test can choose the instant the copy age is measured from.
  final DateTime Function()? clock;

  /// Where the evidence about this device's clock comes from.
  ///
  /// Nullable, and the default is [ContinuityGap.noAnchor] — the *fail-closed*
  /// value, never a claim of continuity. That distinction is the whole reason
  /// this is allowed a default at all while [SnapshotView.continuity] is not:
  /// absence of evidence is a real answer this app can render, and evidence is
  /// not something a default may invent.
  final ClockContinuity Function()? continuity;

  @override
  State<HomePage> createState() => _HomePageState();
}

/// The stand-in until task `22`'s platform monotonic source is wired.
///
/// Named rather than inlined so that `grep` finds the one place this app
/// currently declines to know, and so replacing it is a single edit.
/// `PlatformClockContinuitySource` exists and is tested; giving it the
/// pairing-scoped anchor store it reads against is the slice after this one.
ClockContinuity _noContinuitySource() => const ContinuityUnknown(ContinuityGap.noAnchor);

/// What one screenful needs: the load, the series if it could be read, and
/// whether this payload reached the record.
typedef _Loaded = (HomeLoad load, NetWorthHistory? history, bool recordingFailed);

class _HomePageState extends State<HomePage> {
  late Future<_Loaded> _loaded;

  @override
  void initState() {
    super.initState();
    _loaded = _load();
  }

  /// **A broken series must not take the total off the screen.**
  ///
  /// The number with its age is what this product is for; the curve is what it
  /// adds. So the history's failure is caught here and travels as a `null`,
  /// which the curve renders as "couldn't read the history" — a smaller, truer
  /// statement than the error screen, and one that leaves the headline standing.
  ///
  /// **A record that cannot be written gets the same treatment**, and it is a
  /// third state rather than a second: reading and writing the record fail
  /// independently, and the one this screen would otherwise describe wrongly is
  /// "reads fine, writes nothing".
  ///
  /// [HomeLoader.load] never throws by contract, so unlike the source it
  /// replaced its failures arrive as values and are rendered as themselves. The
  /// error branch in [build] is kept anyway and is now exactly what its name
  /// says: the backstop for a store that breaks that contract, not the screen a
  /// routine unpaired phone lands on.
  Future<_Loaded> _load() async {
    final load = await widget.loader.load();
    // After the await, never before: the recording happens inside the refresh,
    // so reading this first would report the previous launch's outcome for the
    // payload about to be drawn. A refresher that is not a [RecordingStatus]
    // records nothing and so has no failure to report — which is not the same
    // claim as "it recorded successfully", but renders the same and is the only
    // honest thing to say about a build that keeps no record.
    final recordingFailed = switch (widget.loader.refresher) {
      RecordingStatus(:final lastRecordingFailed) => lastRecordingFailed,
      _ => false,
    };
    NetWorthHistory? history;
    try {
      history = await widget.historySource.load();
    } on Object catch (error) {
      // To [debugLog], not the screen and not `debugPrint` — same reasoning as
      // the payload branch below.
      debugLog(() => 'history unreadable: $error');
    }
    return (load, history, recordingFailed);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(l10n.appTitle)),
      body: SafeArea(
        child: FutureBuilder<_Loaded>(
          future: _loaded,
          builder: (context, snapshot) {
            if (snapshot.hasError) {
              // The error object does not reach the screen. It used to, as
              // `'${snapshot.error}'` under the summary, which put raw internal
              // text like `Bad state: no payload` in front of the owner — past
              // the i18n layer, in a shape nobody chose, and with no bound on
              // what an exception might have put in its message.
              //
              // It goes to [debugLog], not to `debugPrint`: the second round of
              // this review found that `debugPrint` still logs in release, so
              // the first fix had moved the unbounded text off the screen and
              // left it in the shipped APK's logcat.
              //
              // And it is passed as a closure rather than a string, because the
              // third round found the first version of that fix still shipping
              // the literal: an argument is evaluated before the callee's gate
              // is reached, so only work placed *inside* the gate can be dead
              // code. See `src/debug_log.dart`.
              debugLog(() => 'snapshot unreadable: ${snapshot.error}');
              return _Message.problem(icon: Icons.error_outline, text: l10n.snapshotUnreadable);
            }
            final loaded = snapshot.data;
            if (loaded == null) {
              return const Center(child: CircularProgressIndicator());
            }
            final (load, history, recordingFailed) = loaded;
            return _body(
              l10n: l10n,
              load: load,
              history: history,
              recordingFailed: recordingFailed,
            );
          },
        ),
      ),
    );
  }

  /// One exhaustive switch over what the phone actually has.
  ///
  /// **No `default`, on either level, and that is load-bearing.** `HomeLoad`
  /// and `HeldCopyState` are sealed, so a state added to either becomes a
  /// compile error here rather than a branch that silently renders the wrong
  /// sentence — which `home_load.dart` names as the reason it carries
  /// [HeldCopyState] itself instead of mirroring it into a parallel hierarchy.
  Widget _body({
    required AppLocalizations l10n,
    required HomeLoad load,
    required NetWorthHistory? history,
    required bool recordingFailed,
  }) {
    switch (load) {
      case HomeNotPaired():
        // Not an error icon and not the error colour: a phone that has never
        // been paired is working exactly as a fresh install should.
        return _Message.neutral(icon: Icons.link_off, text: l10n.homeNotPaired);
      case HomePairingUnreadable():
        return _Message.problem(icon: Icons.lock_outline, text: l10n.homePairingUnreadable);
      case HomePaired(:final copy, :final diagnostics, :final baseline):
        switch (copy) {
          case HeldCopyHeld(:final payload):
            return SnapshotView(
              payload: payload,
              history: history,
              recordingFailed: recordingFailed,
              deviceNow: (widget.clock ?? DateTime.now)(),
              // **Fail-closed, and this is the placeholder, not the answer.**
              // Until the platform monotonic source is given its anchor store
              // this device cannot establish that its clock has run
              // continuously, so it says so. The one default that would change
              // a verdict is the one claiming continuity, which is why it is
              // not the default.
              continuity: (widget.continuity ?? _noContinuitySource)(),
              // **Measured now, not asserted.** These two were
              // `DiagnosticsAbsent()` and `BaselineAbsent()` written at this
              // call site, which was true of a build that performed no fetches
              // and is false of this one. They come off the same
              // `pairing_id` as the copy above, read in the same load.
              diagnostics: diagnostics,
              baseline: baseline,
            );
          case HeldCopyAbsent():
            return _Message.neutral(icon: Icons.inbox_outlined, text: l10n.copyAbsent);
          case HeldCopyUnreadable():
            // The reason is not rendered. It is a diagnostic string built from
            // whatever the document or the platform produced, and the bar for
            // text on this screen is the same one `snapshot.error` failed.
            return _Message.problem(icon: Icons.error_outline, text: l10n.copyUnreadable);
          case HeldCopyNotRead():
            return _Message.problem(icon: Icons.help_outline, text: l10n.copyNotRead);
          case HeldCopyOutdated():
            // Neutral for `held_copy.dart`'s reason: the ordinary cause is that
            // the owner installed a new APK, and raising an alarm about a
            // routine upgrade is the mistake a three-state store would make.
            // Neither version number reaches the screen.
            return _Message.neutral(icon: Icons.update, text: l10n.copyOutdated);
        }
    }
  }
}

/// A screenful that is a sentence rather than a number.
///
/// **Two tones, and the split is whether anything is wrong with this device.**
/// A fresh install holding no copy and a phone whose saved copy is damaged are
/// both "no total on screen", and painting them the same red would tell the
/// first owner his device is broken. The tone is chosen at each call site above,
/// beside the reasoning for it.
class _Message extends StatelessWidget {
  const _Message.problem({required this.icon, required this.text}) : _isProblem = true;

  const _Message.neutral({required this.icon, required this.text}) : _isProblem = false;

  final IconData icon;
  final String text;
  final bool _isProblem;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              icon,
              color: _isProblem ? theme.colorScheme.error : theme.colorScheme.onSurfaceVariant,
            ),
            const SizedBox(height: 12),
            Text(text, style: theme.textTheme.titleMedium, textAlign: TextAlign.center),
          ],
        ),
      ),
    );
  }
}
