import 'package:flutter/material.dart';

import '../../l10n/generated/app_localizations.dart';
import '../data/history_source.dart';
import '../data/snapshot_source.dart';
import '../debug_log.dart';
import '../domain/clock_continuity.dart';
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
class HomePage extends StatefulWidget {
  const HomePage({
    super.key,
    required this.source,
    required this.historySource,
    this.clock,
    this.continuity,
  });

  /// Where the payload comes from — and, when it is a [RecordingStatus], the
  /// answer to whether that payload was kept.
  ///
  /// **Asked of the source rather than wired separately, and that is the fix
  /// for a defect this had in its first draft.** The recording outcome arrived
  /// as its own optional constructor argument, which meant a caller that wired
  /// the recorder and forgot the second seam got the reassuring answer by
  /// default — a build that loses every reading, silently, exactly as before.
  /// The reviewer's own probe wired it that way, which is the evidence that it
  /// is the shape a caller reaches for. One object cannot be half-wired.
  final SnapshotSource source;

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

/// The stand-in until task `22`'s platform monotonic source exists.
///
/// Named rather than inlined so that `grep` finds the one place this app
/// currently declines to know, and so replacing it is a single edit.
ClockContinuity _noContinuitySource() => const ContinuityUnknown(ContinuityGap.noAnchor);

/// What one screenful needs: the payload, the series if it could be read, and
/// whether this payload reached the record.
typedef _Loaded = (PhonePayload payload, NetWorthHistory? history, bool recordingFailed);

class _HomePageState extends State<HomePage> {
  late Future<_Loaded> _payload;

  @override
  void initState() {
    super.initState();
    _payload = _load();
  }

  /// **A broken series must not take the total off the screen.**
  ///
  /// The number with its age is what this product is for; the curve is what it
  /// adds. So the history's failure is caught here and travels as a `null`,
  /// which the curve renders as "couldn't read the history" — a smaller, truer
  /// statement than the error screen, and one that leaves the headline standing.
  /// The payload's failure is not caught: there is nothing to show without it.
  ///
  /// **A record that cannot be written gets the same treatment**, and it is a
  /// third state rather than a second: reading and writing the record fail
  /// independently, and the one this screen would otherwise describe wrongly is
  /// "reads fine, writes nothing".
  Future<_Loaded> _load() async {
    final source = widget.source;
    final payload = await source.load();
    // After the await, never before: the recording happens inside `load()`, so
    // reading this first would report the previous payload's outcome for the one
    // about to be drawn. A source that is not a [RecordingStatus] records
    // nothing and so has no failure to report — which is not the same claim as
    // "it recorded successfully", but renders the same and is the only honest
    // thing to say about a build that keeps no record.
    final recordingFailed = switch (source) {
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
    return (payload, history, recordingFailed);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(l10n.appTitle)),
      body: SafeArea(
        child: FutureBuilder<_Loaded>(
          future: _payload,
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
              return _Message(icon: Icons.error_outline, text: l10n.snapshotUnreadable);
            }
            final loaded = snapshot.data;
            if (loaded == null) {
              return const Center(child: CircularProgressIndicator());
            }
            final (payload, history, recordingFailed) = loaded;
            return SnapshotView(
              payload: payload,
              history: history,
              recordingFailed: recordingFailed,
              deviceNow: (widget.clock ?? DateTime.now)(),
              // **Fail-closed, and this is the placeholder, not the answer.**
              // Task `22` still owes the platform monotonic source; until it
              // lands this device cannot establish that its clock has run
              // continuously, so it says so. The one default that would change
              // a verdict is the one claiming continuity, which is why it is
              // not the default.
              continuity: (widget.continuity ?? _noContinuitySource)(),
            );
          },
        ),
      ),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, color: theme.colorScheme.error),
            const SizedBox(height: 12),
            Text(text, style: theme.textTheme.titleMedium, textAlign: TextAlign.center),
          ],
        ),
      ),
    );
  }
}
