import 'package:flutter/material.dart';

import '../domain/copy_freshness.dart';
import '../domain/phone_payload.dart';
import 'headline.dart';
import 'instant.dart';

/// One published snapshot, with both staleness dimensions kept apart.
///
/// Invariant **I4**: the number can be old for two independent reasons — the
/// *connection* to an institution died, or this *phone's copy* of the snapshot
/// is old — and they have different fixes, so they get two rows and never one
/// combined badge. Task 22 deepens this treatment; it does not introduce it,
/// which is why the distinction is built in here rather than deferred.
class SnapshotView extends StatelessWidget {
  const SnapshotView({super.key, required this.payload, required this.deviceNow});

  final PhonePayload payload;

  /// Injected rather than read from the clock inside `build`, so the copy
  /// dimension is testable at a chosen instant and a widget test never depends
  /// on the day it runs.
  final DateTime deviceNow;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Headline(total: payload.total),
          const SizedBox(height: 28),
          const Divider(height: 1),
          const SizedBox(height: 16),
          _Dimension(
            icon: _connectionIcon(payload.connectionState),
            label: 'Accounts',
            detail: _connectionText(payload.connectionState),
            isWarning: payload.connectionState != ConnectionDisplayState.ok,
          ),
          const SizedBox(height: 12),
          _Dimension(
            icon: Icons.phone_iphone,
            label: 'This copy',
            detail: _copyText(payload, deviceNow),
            isWarning: payload.copyFreshness(deviceNow) != CopyFreshness.fresh,
          ),
        ],
      ),
    );
  }
}

class _Dimension extends StatelessWidget {
  const _Dimension({
    required this.icon,
    required this.label,
    required this.detail,
    required this.isWarning,
  });

  final IconData icon;
  final String label;
  final String detail;
  final bool isWarning;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colour = isWarning ? theme.colorScheme.error : theme.colorScheme.onSurfaceVariant;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 18, color: colour),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label, style: theme.textTheme.labelMedium),
              Text(detail, style: theme.textTheme.bodySmall?.copyWith(color: colour)),
            ],
          ),
        ),
      ],
    );
  }
}

IconData _connectionIcon(ConnectionDisplayState state) => switch (state) {
      ConnectionDisplayState.ok => Icons.link,
      ConnectionDisplayState.waiting => Icons.hourglass_empty,
      ConnectionDisplayState.actionNeeded => Icons.link_off,
    };

/// §9.2 splits the connection axis precisely so `WAITING` can never read as a
/// demand to re-link — only `ACTION_NEEDED` invites one.
String _connectionText(ConnectionDisplayState state) => switch (state) {
      ConnectionDisplayState.ok => 'all reporting normally',
      ConnectionDisplayState.waiting => 'some data is behind — nothing for you to do',
      ConnectionDisplayState.actionNeeded => 'an account needs to be reconnected',
    };

String _copyText(PhonePayload payload, DateTime deviceNow) =>
    switch (payload.copyFreshness(deviceNow)) {
      CopyFreshness.fresh => 'published ${formatInstantUtc(payload.publishedAt)}',
      CopyFreshness.stale =>
        'overdue — nothing new since ${formatInstantUtc(payload.publishedAt)}',
      CopyFreshness.unknown => "this device's clock disagrees with the server's",
    };
