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
import '../pairing/pairing_intake.dart';
import 'pairing_intake_page.dart';
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
    required this.intake,
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

  /// The way in to §6.3 step 2 — **required, like the two seams above it.**
  ///
  /// No default, for `NetWorthApp`'s reason: a build that forgot to wire this
  /// would compile, run, and render *"this phone isn't paired yet"* with no way
  /// to stop doing so, which is the exact defect `21a` exists to close
  /// (`tasks/README.md`). A missing argument is a better way to find that out
  /// than an owner with a permanently unpaired phone.
  final PairingIntake intake;

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

  /// Whether the intake route is already on the stack.
  ///
  /// A second tap delivered before the push settles would put two forms over one
  /// vault, and the second one's dismissal would then pop back to the first
  /// rather than to this screen.
  bool _intakeOpen = false;

  @override
  void initState() {
    super.initState();
    _loaded = _load();
  }

  /// Opens §6.3 step 2 and, **only if the vault actually took a bundle**, loads
  /// the screen again from the pairing that is now stored.
  ///
  /// Three things about this are the row's acceptance rather than style:
  ///
  /// - **The reload is conditional on the form's answer.** A dismissal pops
  ///   `null` and a failed submission never pops at all, so neither reaches the
  ///   `setState` below. A screen that reloaded on every return would put an
  ///   unpaired phone through a fetch it has no key for and report the result as
  ///   though something had been tried.
  /// - **The write is already awaited by the time this resumes.**
  ///   `PairingIntake.submit` awaits `PairingVault.provision`, which awaits the
  ///   protected write, and the form pops only after that returns — so the
  ///   refresh below cannot read a vault that is still being written.
  /// - **Reassigning `_loaded` is what isolates an old attempt**, and it is the
  ///   whole of that isolation on this side. A refresh that was in flight across
  ///   the replacement finishes against the future this line just replaced, and
  ///   `FutureBuilder` has dropped it: it renders the future it is given now, so
  ///   the earlier load cannot arrive late and take over the new pairing's
  ///   screen. What it filed while it ran is scoped by the `pairing_id` its own
  ///   reader read, which is the other half, and it lives in `snapshot_refresh.dart`.
  Future<void> _openIntake({required bool isReplacement}) async {
    if (_intakeOpen) {
      return;
    }
    _intakeOpen = true;
    try {
      final stored = await Navigator.of(context).push<bool>(
        MaterialPageRoute(
          builder: (_) =>
              PairingIntakePage(intake: widget.intake, isReplacement: isReplacement),
        ),
      );
      if (!mounted || stored != true) {
        return;
      }
      setState(() {
        _loaded = _load();
      });
    } finally {
      _intakeOpen = false;
    }
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

  /// **The load is above the `Scaffold`, not inside its body, and that is this
  /// commit's one structural change.**
  ///
  /// The replace-pairing action belongs in the app bar: it is the only place
  /// reachable from all five of `HomePaired`'s copy states, and putting it in the
  /// body would mean adding it to five branches, one of which is
  /// [SnapshotView]'s whole screenful. But it must appear *only* when a pairing
  /// exists — offering to replace one on a phone that has none contradicts the
  /// sentence under it — and whether one exists is a fact the load carries. So
  /// the app bar cannot be built before the load resolves, and the
  /// [FutureBuilder] moves up rather than the state moving down.
  @override
  Widget build(BuildContext context) {
    return FutureBuilder<_Loaded>(
      // **Keyed on the future, and this is the fix for a defect the `21a` tests
      // found rather than a precaution.** `FutureBuilder.didUpdateWidget`
      // unsubscribes from the old future and subscribes to the new one, but it
      // carries the old `AsyncSnapshot`'s *data* across — only the connection
      // state is reset. So replacing a pairing left the previous pairing's total
      // on screen for the whole of the new load: the phone was paired to B and
      // rendering A's figure, under A's age annotation, with no way for the owner
      // to tell. A number belonging to a relationship that has ended is the one
      // thing this product exists not to render.
      //
      // An [ObjectKey] over the future makes a new future a new element, so the
      // reload starts from no data and renders the loading state it is in. Same
      // family as the reused `State` that made the six-state test read one
      // screen six times — a widget's identity is not its position.
      key: ObjectKey(_loaded),
      future: _loaded,
      builder: _scaffold,
    );
  }

  Widget _scaffold(BuildContext context, AsyncSnapshot<_Loaded> snapshot) {
    final l10n = AppLocalizations.of(context);
    final loaded = snapshot.data;
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.appTitle),
        actions: [
          // Only over a pairing that was read. Absent while loading, absent on
          // the error backstop, and absent on `HomePairingUnreadable` — the
          // `HomePairingUnreadable` branch of [_body] says why that last one is
          // a decision and not an oversight.
          if (loaded != null && loaded.$1 is HomePaired)
            IconButton(
              key: const Key('replace-pairing'),
              icon: const Icon(Icons.link),
              tooltip: l10n.pairingReplaceTitle,
              onPressed: () => _openIntake(isReplacement: true),
            ),
        ],
      ),
      body: SafeArea(child: _content(l10n, snapshot)),
    );
  }

  Widget _content(AppLocalizations l10n, AsyncSnapshot<_Loaded> snapshot) {
    if (snapshot.hasError) {
      // The error object does not reach the screen. It used to, as
      // `'${snapshot.error}'` under the summary, which put raw internal text
      // like `Bad state: no payload` in front of the owner — past the i18n
      // layer, in a shape nobody chose, and with no bound on what an exception
      // might have put in its message.
      //
      // It goes to [debugLog], not to `debugPrint`: the second round of this
      // review found that `debugPrint` still logs in release, so the first fix
      // had moved the unbounded text off the screen and left it in the shipped
      // APK's logcat.
      //
      // And it is passed as a closure rather than a string, because the third
      // round found the first version of that fix still shipping the literal:
      // an argument is evaluated before the callee's gate is reached, so only
      // work placed *inside* the gate can be dead code. See `src/debug_log.dart`.
      debugLog(() => 'snapshot unreadable: ${snapshot.error}');
      return _Message.problem(icon: Icons.error_outline, text: l10n.snapshotUnreadable);
    }
    final loaded = snapshot.data;
    if (loaded == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final (load, history, recordingFailed) = loaded;
    return _body(l10n: l10n, load: load, history: history, recordingFailed: recordingFailed);
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
        //
        // **The only state that carries the first-pairing button.** This is the
        // one screen an owner with a fresh install can reach, and before `21a`
        // it was terminal — nothing in `app/lib/` called
        // `PairingVault.provision`, so this sentence was the whole app forever.
        return _Message.neutral(
          icon: Icons.link_off,
          text: l10n.homeNotPaired,
          action: _IntakeButton(
            label: l10n.pairingIntakeTitle,
            onPressed: () => _openIntake(isReplacement: false),
          ),
        );
      case HomePairingUnreadable():
        // **No intake button here, and it is a decision rather than an
        // omission.** This state is "protected storage did not answer", not "no
        // pairing" — `home_load.dart` keeps the two apart precisely because
        // sending an owner whose device is fine to re-pair is the one action
        // that takes the copy he still has off his screen. And a store that
        // cannot be read is overwhelmingly a store that cannot be written, so
        // the button would offer a cure that ends in `PairingIntakeNotStored`.
        // The acceptance asks for a form on an unpaired install and a replace
        // action on a paired one; this is neither.
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
  const _Message.problem({required this.icon, required this.text})
    : _isProblem = true,
      action = null;

  const _Message.neutral({required this.icon, required this.text, this.action})
    : _isProblem = false;

  final IconData icon;
  final String text;

  /// What the owner can do about it, when there is something.
  ///
  /// Only the neutral constructor takes one, which is not a restriction so much
  /// as a description of the five call sites: the four problem states are a
  /// damaged copy, an unreadable copy, an unreadable pairing and a broken
  /// collaborator, and none of them has an action this app can offer. The one
  /// state with a button is the one that is not a problem at all.
  final Widget? action;
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
              // **Keyed, and `21a` is why.** The tone test reads this icon's
              // colour, and it used to find it with `find.byType(Icon)` — which
              // was unambiguous only while the screen had exactly one icon on it.
              // The app bar's replace-pairing action is a second one, so that
              // finder now matches two widgets over every paired state, and a
              // finder that silently picks between them is worse than one that
              // names what it wants.
              key: const Key('message-icon'),
              icon,
              color: _isProblem ? theme.colorScheme.error : theme.colorScheme.onSurfaceVariant,
            ),
            const SizedBox(height: 12),
            Text(text, style: theme.textTheme.titleMedium, textAlign: TextAlign.center),
            if (action != null) ...[const SizedBox(height: 24), action!],
          ],
        ),
      ),
    );
  }
}

/// The button that opens §6.3 step 2 from the not-paired screen.
///
/// Its own widget so that the key it carries is in one place: the tests find the
/// way into intake by that key rather than by the copy, which is what lets the
/// wording change without touching them.
class _IntakeButton extends StatelessWidget {
  const _IntakeButton({required this.label, required this.onPressed});

  final String label;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) => FilledButton(
    key: const Key('open-pairing-intake'),
    onPressed: onPressed,
    child: Text(label),
  );
}
