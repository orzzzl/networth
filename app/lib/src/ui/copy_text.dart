import '../../l10n/generated/app_localizations.dart';
import '../domain/copy_freshness.dart';
import '../domain/fetch_diagnostics.dart';
import 'instant.dart';

/// The words for §9.1's copy verdict — the whole matrix, as two pure functions.
///
/// **Its own file, and public, because the widget path cannot reach most of it.**
/// There are twelve distinct sentences here, and this build performs no fetches,
/// so its diagnostics are [DiagnosticsAbsent] and a widget test can only ever
/// drive four: fresh, the three clock disagreements, and `NeverFetched`. The
/// other eight — every [FetchFailureClass], `NotCheckedSinceDue`,
/// `RecordsUnusable`, `ServedPayloadNotHeld` and `HostNotPublishing` — become
/// reachable on screen only when this task's transport starts writing records.
///
/// Left private inside the view they would have been eight strings nothing could
/// pin until then, which is how the sentence this change exists to delete got
/// into the shipped build in the first place: it was *computed* correctly and
/// then rendered by a branch no test looked at. Mapping a verdict to a sentence
/// is a unit; laying it out is the widget's job.

/// The copy row's own claim: what is on screen, and how old it is.
///
/// **No cause appears here**, which is the shape of the fix. Through task `22`
/// this line read *"overdue — nothing new since `<t>`"* for every stale copy, and
/// that sentence is §9.1's `HOST_NOT_PUBLISHING` — a claim about the *server*.
/// The predicate reaches `COPY_STALE` by two routes, and the other one,
/// `CANNOT_CHECK`, means the phone could not reach the server at all. Saying
/// "nothing new has been published" there accuses a host this device may never
/// have spoken to, which is the exact misattribution §9.1 exists to prevent.
/// The cause belongs to [copyReasonText], where exactly one branch may mention
/// the host.
String copyDetailText(AppLocalizations l10n, CopyState copy, DateTime publishedAt) {
  final published = formatInstantUtc(l10n, publishedAt);
  return switch (copy) {
    CopyFresh() => l10n.copyFresh(published),
    CopyStale() => l10n.copyStale(published),
    // "dated", not "from": the whole state is that this device cannot place the
    // copy in time, and "from" quietly claims the age the next line disclaims.
    CopyUnknown() => l10n.copyUnknownShowing(published),
  };
}

/// Why, in the owner's terms — one sentence per thing he might do about it.
///
/// `null` for a fresh copy: there is nothing further to say, and a row that
/// always carries a second line teaches the eye to skip the second line.
String? copyReasonText(AppLocalizations l10n, CopyState copy) => switch (copy) {
      CopyFresh() => null,
      CopyUnknown(:final disagreement) => switch (disagreement) {
          ClockDisagreement.payloadFromTheFuture => l10n.copyUnknownFuture,
          ClockDisagreement.deviceClockMovedBackwards => l10n.copyUnknownClockMovedBackwards,
          ClockDisagreement.clockContinuityUnknown => l10n.copyUnknownClockUnconfirmed,
        },
      // **The one branch allowed to speak about the server**, because it is the
      // one where all three of §9.1's conjuncts held: the phone reached the host
      // and the host offered nothing newer. Its confirmation instant is inside
      // its own sentence, so it never takes the `last checked` suffix — the
      // suffix would restate the same instant twice.
      CopyStale(reason: HostNotPublishing(:final confirmedAt)) =>
        l10n.copyReasonHostNotPublishing(formatInstantUtc(l10n, confirmedAt)),
      CopyStale(reason: CannotCheck(:final cause, :final since)) =>
        _withLastChecked(l10n, _cannotCheckText(l10n, cause), since),
    };

/// Appends the last successful check, when there was one.
///
/// [since] is `null` on a phone that has attempted under this pairing and never
/// succeeded — a real state, and a different one from never having attempted.
/// Nothing is substituted for it: an invented instant would be the same lie the
/// nullable itself exists to refuse, one layer up.
String _withLastChecked(AppLocalizations l10n, String reason, DateTime? since) =>
    since == null ? reason : l10n.copyReasonLastChecked(reason, formatInstantUtc(l10n, since));

String _cannotCheckText(AppLocalizations l10n, CannotCheckCause cause) => switch (cause) {
      NeverFetched() => l10n.copyReasonNeverFetched,
      NotCheckedSinceDue() => l10n.copyReasonNotCheckedSinceDue,
      ServedPayloadNotHeld() => l10n.copyReasonServedPayloadNotHeld,
      // The sentence carries none of the stored bytes and names no path: this is
      // the owner's screen, and `RecordsUnusable.reason` is `debugLog`'s.
      RecordsUnusable() => l10n.copyReasonRecordsUnusable,
      FetchFailed(:final errorClass) => switch (errorClass) {
          FetchFailureClass.offline => l10n.copyReasonOffline,
          FetchFailureClass.hostUnreachable => l10n.copyReasonHostUnreachable,
          FetchFailureClass.credentialRejected => l10n.copyReasonCredentialRejected,
          FetchFailureClass.transportError => l10n.copyReasonTransportError,
        },
    };
