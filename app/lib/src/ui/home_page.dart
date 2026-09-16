import 'package:flutter/material.dart';

import '../../l10n/generated/app_localizations.dart';
import '../data/snapshot_source.dart';
import '../debug_log.dart';
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
  const HomePage({super.key, required this.source, this.clock});

  final SnapshotSource source;

  /// Overridable so a test can choose the instant the copy age is measured from.
  final DateTime Function()? clock;

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  late Future<PhonePayload> _payload;

  @override
  void initState() {
    super.initState();
    _payload = widget.source.load();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(l10n.appTitle)),
      body: SafeArea(
        child: FutureBuilder<PhonePayload>(
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
            final payload = snapshot.data;
            if (payload == null) {
              return const Center(child: CircularProgressIndicator());
            }
            return SnapshotView(
              payload: payload,
              deviceNow: (widget.clock ?? DateTime.now)(),
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
