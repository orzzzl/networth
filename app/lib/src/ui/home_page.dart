import 'package:flutter/material.dart';

import '../data/snapshot_source.dart';
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
    return Scaffold(
      appBar: AppBar(title: const Text('Net worth')),
      body: SafeArea(
        child: FutureBuilder<PhonePayload>(
          future: _payload,
          builder: (context, snapshot) {
            if (snapshot.hasError) {
              return _Message(
                icon: Icons.error_outline,
                text: "couldn't read the published snapshot",
                detail: '${snapshot.error}',
              );
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
  const _Message({required this.icon, required this.text, required this.detail});

  final IconData icon;
  final String text;
  final String detail;

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
            const SizedBox(height: 6),
            Text(
              detail,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}
