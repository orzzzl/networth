import 'package:flutter/material.dart';

import '../../l10n/generated/app_localizations.dart';
import '../domain/clock_continuity.dart';
import '../domain/copy_freshness.dart';
import '../domain/net_worth_history.dart';
import '../domain/phone_payload.dart';
import 'copy_text.dart';
import 'headline.dart';
import 'history_curve.dart';

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
    required this.recordingFailed,
    required this.deviceNow,
    required this.continuity,
  });

  final PhonePayload payload;

  /// The curve's series, or null when it could not be read.
  final NetWorthHistory? history;

  /// Whether the reading now on screen failed to reach the record.
  ///
  /// Required rather than defaulted, for the reason the headline currency below
  /// is: a caller that does not know cannot claim it is `false`, and `false` is
  /// the reassuring answer.
  final bool recordingFailed;

  /// Whether this device's wall clock can be trusted to age the copy.
  ///
  /// Required for the same reason as [recordingFailed], and the reason is
  /// sharper here: the only default that would not change a verdict is the one
  /// asserting the clock is fine, and a caller that cannot measure that must
  /// not be able to claim it. [ContinuityUnknown] renders as *"couldn't
  /// check"*, which is the honest surface while the platform source task `22`
  /// still owes is unbuilt.
  final ClockContinuity continuity;

  /// Injected rather than read from the clock inside `build`, so the copy
  /// dimension is testable at a chosen instant and a widget test never depends
  /// on the day it runs.
  final DateTime deviceNow;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    // **The whole verdict, evaluated once.** Both rows read it — the copy row for
    // its own claim and the connection row to decide whether its state is
    // historical — and evaluating it twice would let them disagree about the same
    // instant. It is the `CopyState` and not the [CopyFreshness] enum because the
    // enum is the indicator only: the reason travels beside it, and dropping it
    // here is what made the copy row assert `HOST_NOT_PUBLISHING` over every
    // stale copy (see `copyStale`'s note in the ARB).
    final copy = payload.copyState(deviceNow, continuity: continuity);
    // **Scrollable, because this screen's height is not ours to choose.** The
    // curve made the content taller than a 640x360 landscape viewport and the
    // `Column` rendered overflow stripes — and the same arithmetic fails in
    // portrait as soon as the owner raises the system text size, which is a
    // setting people who care about reading numbers actually use. A fixed
    // layout that happens to fit the test device is not a layout; the content
    // is what it is, and the screen must be able to show all of it.
    return SingleChildScrollView(
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
            // §9.2 rule 3: this state came out of the payload, so over a copy
            // that is not current it describes then and not now. Shown only in
            // that case — over a fresh copy the qualifier is noise, and the rule
            // exists for the case where the two actually differ.
            reason: copy is CopyFresh ? null : l10n.connectionAsOfCopy,
            isWarning: payload.connectionState != ConnectionDisplayState.ok,
          ),
          const SizedBox(height: 12),
          _Dimension(
            icon: Icons.phone_iphone,
            label: l10n.thisCopyLabel,
            detail: copyDetailText(l10n, copy, payload.publishedAt),
            reason: copyReasonText(l10n, copy),
            isWarning: copy is! CopyFresh,
          ),
          // Last, deliberately. The order on this screen is the order of the
          // claims: the number, its age, the two reasons it could be old, and
          // only then its shape over time.
          const SizedBox(height: 24),
          // The headline's currency travels with the series, because this is the
          // only place both are in scope. `NetWorthHistory.reduce` refuses a
          // series that mixes currencies internally; it cannot see a series that
          // agrees with itself and disagrees with the total above it, and a
          // figure-less curve of the wrong quantity looks exactly like a right
          // one.
          HistoryCurve(
            history: history,
            recordingFailed: recordingFailed,
            headlineCurrency: payload.total.amount.currency,
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
    required this.reason,
    required this.isWarning,
  });

  final IconData icon;
  final String label;
  final String detail;

  /// The second line, or `null` when there is nothing further to say.
  ///
  /// **Required rather than defaulted to `null`**, for the reason the two seams
  /// on [SnapshotView] are: a row whose cause is the point of the row is a row a
  /// caller must not be able to build without deciding about it. Omitting the
  /// argument is how the reason gets computed and then dropped, which is the
  /// defect this change exists to fix.
  final String? reason;

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
              // Its own `Text`, never concatenated into the line above: the
              // claim and its cause are two facts, they wrap independently at
              // large system text sizes, and a screen reader announces them as
              // two things to weigh rather than one long sentence.
              if (reason case final String text)
                Text(
                  text,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
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
