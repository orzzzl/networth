import 'package:flutter/material.dart';

import '../../l10n/generated/app_localizations.dart';
import '../domain/copy_freshness.dart';
import '../domain/net_worth_history.dart';
import '../domain/phone_payload.dart';
import 'headline.dart';
import 'history_curve.dart';
import 'instant.dart';

/// One published snapshot, with both staleness dimensions kept apart.
///
/// Invariant **I4**: the number can be old for two independent reasons — the
/// *connection* to an institution died, or this *phone's copy* of the snapshot
/// is old — and they have different fixes, so they get two rows and never one
/// combined badge. Task 22 deepens this treatment; it does not introduce it,
/// which is why the distinction is built in here rather than deferred.
class SnapshotView extends StatelessWidget {
  const SnapshotView({
    super.key,
    required this.payload,
    required this.history,
    required this.deviceNow,
  });

  final PhonePayload payload;

  /// The curve's series, or null when it could not be read.
  final NetWorthHistory? history;

  /// Injected rather than read from the clock inside `build`, so the copy
  /// dimension is testable at a chosen instant and a widget test never depends
  /// on the day it runs.
  final DateTime deviceNow;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
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
            label: l10n.accountsLabel,
            detail: _connectionText(l10n, payload.connectionState),
            isWarning: payload.connectionState != ConnectionDisplayState.ok,
          ),
          const SizedBox(height: 12),
          _Dimension(
            icon: Icons.phone_iphone,
            label: l10n.thisCopyLabel,
            detail: _copyText(l10n, payload, deviceNow),
            isWarning: payload.copyFreshness(deviceNow) != CopyFreshness.fresh,
          ),
          // Last, deliberately. The order on this screen is the order of the
          // claims: the number, its age, the two reasons it could be old, and
          // only then its shape over time.
          const SizedBox(height: 24),
          HistoryCurve(history: history),
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
/// demand to re-link — only `ACTION_NEEDED` invites action at all.
///
/// `ACTION_NEEDED` deliberately does not name the remedy. The producer returns it
/// for three different causes — an unreconciled account, an institution needing
/// re-authentication, and a frozen source clock (`networth/staleness.py`) —
/// whose next actions are reconciliation, reconnection and neither. The earlier
/// wording, "an account needs to be reconnected", named the second one for all
/// three, so two thirds of the time it sent the owner to the wrong place. This
/// payload carries one enum and no cause, so the copy stops where the wire does;
/// task 22 parses enough detail to say more.
String _connectionText(AppLocalizations l10n, ConnectionDisplayState state) =>
    switch (state) {
      ConnectionDisplayState.ok => l10n.connectionOk,
      ConnectionDisplayState.waiting => l10n.connectionWaiting,
      ConnectionDisplayState.actionNeeded => l10n.connectionActionNeeded,
    };

String _copyText(AppLocalizations l10n, PhonePayload payload, DateTime deviceNow) =>
    switch (payload.copyFreshness(deviceNow)) {
      CopyFreshness.fresh => l10n.copyFresh(formatInstantUtc(l10n, payload.publishedAt)),
      CopyFreshness.stale => l10n.copyStale(formatInstantUtc(l10n, payload.publishedAt)),
      CopyFreshness.unknown => l10n.copyUnknown,
    };
