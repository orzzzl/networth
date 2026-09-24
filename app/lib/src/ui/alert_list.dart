import 'package:flutter/material.dart';

import '../../l10n/generated/app_localizations.dart';
import '../domain/payload_alert.dart';

/// §11's open alert set, on the only surface that can ever show it.
///
/// **The channel decision is what shapes this widget.** The owner declined mail
/// and the agent-mailbox route, and push would need infrastructure §4 forbids
/// paying for, so §11 states the consequence plainly: *"There is no way to reach
/// the owner. An alert is seen when he opens the app, and not before."* The rule
/// that follows — *"an unhealthy state must be impossible to miss on open"* — is
/// why this is a filled block above the fold rather than a fourth grey row among
/// the three below it.
///
/// It renders **nothing at all** when the set is empty, which is the ordinary
/// case. An empty "Action needed" heading is a worse screen than no heading: it
/// invites a glance every time and teaches the owner that the heading means
/// nothing, which is the exact habit this section cannot afford.
class AlertList extends StatelessWidget {
  const AlertList({super.key, required this.alerts});

  final List<PayloadAlert> alerts;

  @override
  Widget build(BuildContext context) {
    final summaries = summarizeAlerts(alerts);
    if (summaries.isEmpty) {
      return const SizedBox.shrink();
    }

    final theme = Theme.of(context);
    final l10n = AppLocalizations.of(context);
    final colour = theme.colorScheme.onErrorContainer;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: theme.colorScheme.errorContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            l10n.alertsLabel,
            style: theme.textTheme.labelMedium?.copyWith(color: colour),
          ),
          for (final summary in summaries)
            for (final line in _lines(l10n, summary))
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(_icon(summary.kind), size: 18, color: colour),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        line,
                        style: theme.textTheme.bodyMedium?.copyWith(color: colour),
                      ),
                    ),
                  ],
                ),
              ),
        ],
      ),
    );
  }
}

/// The text for one summary — usually one line, and more than one only for a
/// kind this build has no copy for.
///
/// A known kind renders this app's own localized sentence with the subject count
/// in it, the way `connection_state` already renders through the ARB rather than
/// as host prose. An **unknown** kind renders the host's own sentences instead:
/// they are written for the owner (`networth/alerts.py::_MESSAGES`), they are the
/// only description of a condition this build cannot name, and showing them is
/// the difference between "something is wrong and here is what the host said"
/// and a silently shorter list. They are not localized, and that is the honest
/// trade — English prose the owner can act on beats a localized nothing.
List<String> _lines(AppLocalizations l10n, AlertSummary summary) {
  final count = summary.subjectCount;
  return switch (summary.kind) {
    AlertKind.needsReauth => [l10n.alertNeedsReauth(count)],
    AlertKind.revoked => [l10n.alertRevoked(count)],
    AlertKind.frozenData => [l10n.alertFrozenData(count)],
    AlertKind.pendingReconciliation => [l10n.alertPendingReconciliation(count)],
    AlertKind.shareCountUnconfirmed => [l10n.alertShareCountUnconfirmed(count)],
    null => summary.hostMessages,
  };
}

/// One icon per kind, and a deliberately unalarming one for a kind we cannot
/// name — the row is already inside a block coloured for attention, and an icon
/// asserting a specific fault about a condition this build does not understand
/// would be the widget claiming more than it knows.
IconData _icon(AlertKind? kind) => switch (kind) {
      AlertKind.needsReauth => Icons.lock_reset,
      AlertKind.revoked => Icons.link_off,
      AlertKind.frozenData => Icons.ac_unit,
      AlertKind.pendingReconciliation => Icons.merge_type,
      AlertKind.shareCountUnconfirmed => Icons.pin_outlined,
      null => Icons.info_outline,
    };
