import 'phone_payload.dart';

/// What this phone found when it looked for the copy it is showing.
///
/// **The copy is the thing the product shows on a bad day.** §9.2's whole
/// `COPY_STALE` row is *"Showing a copy from 2 days ago"* with its reason, §9.1's
/// `CANNOT_CHECK` branch describes a phone that holds a copy and could not
/// refresh it, and `DESIGN.md` §9 states it outright: *"**Offline** is not a
/// special case: fetches fail, the cached payload keeps aging."* A build that
/// fetches but keeps nothing has no bad-day screen at all — it has a good-day
/// screen and an error — which is the one axis this task exists for.
///
/// It is also what makes I6 affordable. §9.3's table answers `seq < last_seq`
/// with *"**refuse.** Keep the newer cached payload"*, and those two halves are
/// one sentence for a reason: without something to keep, refusing a downgrade
/// empties the screen while accepting it would fill it, so the secure choice
/// would be the one that costs the owner his number. A control with that shape
/// does not survive contact with a real owner.
///
/// **Four outcomes, and the fourth is the one a three-state version gets
/// wrong.** [HeldCopyAbsent] and [HeldCopyUnreadable] are the split this
/// directory already makes everywhere (`stored_file.dart`, `BaselineState`,
/// `DiagnosticsState`): a missing record must never be readable as a satisfied
/// one, and a damaged record must never be readable as a missing one. What
/// those three cannot say is *"intact, and not in a language this build
/// speaks"* — see [HeldCopyOutdated].
sealed class HeldCopyState {
  const HeldCopyState();
}

/// This phone is holding no copy under this pairing.
///
/// The state a fresh install is in, and the state a re-paired phone is in: a
/// copy stored under a previous `pairing_id` is scoped away to here rather than
/// shown, because the payload key changed with the pairing and the figures
/// behind that copy belong to a relationship that has ended. It is not damage
/// and there is nothing to report — the next successful fetch fills it.
final class HeldCopyAbsent extends HeldCopyState {
  const HeldCopyAbsent();
}

/// The copy, parsed back by the same code that parsed it off the wire.
final class HeldCopyHeld extends HeldCopyState {
  const HeldCopyHeld(this.payload);

  final PhonePayload payload;
}

/// A copy is stored and this build cannot read it. **Never absence.**
///
/// Damage: truncated by a power loss mid-write, corrupted on disk, or a
/// directory this app can no longer search. The owner is holding a phone whose
/// saved copy is gone, and the honest screen says that rather than *"nothing
/// has ever been fetched"*, which is a claim about his history with nothing
/// behind it.
final class HeldCopyUnreadable extends HeldCopyState {
  const HeldCopyUnreadable(this.reason);

  /// What was wrong, for the screen and the log. **Carries no stored bytes** —
  /// the file is a decrypted payload, so quoting it here would put real figures
  /// on the path to `debugLog` and to any surface that renders a reason.
  final String reason;
}

/// Scoped to this pairing, and carrying a **syntactically valid**
/// `schema_version` this build does not read.
///
/// **What it does not claim.** Not that the rest of the document is intact:
/// this build cannot parse a schema it does not know, so it has no way to
/// inspect the body and does not pretend to. Not, strictly, that an upgrade
/// happened — only that the version is a well-formed one that is not
/// [PhonePayload.supportedSchemaVersion]. Everything below is about which
/// explanation is *worth telling the owner*, and it stays the best one, but the
/// state is evidence about a single field and nothing wider.
///

/// **Why this is not [HeldCopyUnreadable].** The two are told apart by the
/// parser with certainty rather than by inference, and they are opposite kinds
/// of news. Damage means something went wrong on this device. This means the
/// owner installed a new APK — the ordinary, expected event — and the copy
/// saved by the previous one is in the previous one's format. Reporting that as
/// a damaged record raises an alarm about a routine upgrade, and this file is
/// the one the row's alert surface reads. The action is identical (wait for the
/// next successful fetch, which overwrites it), and this codebase's test for a
/// class is *what should he be told*, not only *what should he do*:
/// `FetchFailureClass.offline` earns its place on exactly that basis.
///
/// It is **not hypothetical and not distant**. The per-account label contract
/// settled on 2026-09-24 requires bumping `schema_version`, so the first build
/// that carries it meets this state on every phone that had fetched before the
/// upgrade. A three-state store would have told every one of those owners that
/// their saved copy was damaged.
///
/// Both directions land here, deliberately: a copy newer than this build
/// (an APK rolled back) is as unreadable as an older one, and for the same
/// reason. The stored version is reported so a log can say which. **Today only
/// the newer direction is reachable** — `PayloadEnvelope`'s canonical decimal
/// starts at `1` and this build reads `1`, so no valid older spelling exists
/// yet. The rule is stated for the version after next rather than describing a
/// case that can occur now.
///
/// A malformed spelling — `0`, `01`, ` 2 `, arbitrary text — is **not** this
/// state. `FileHeldCopyStore.read` rejects those as [HeldCopyUnreadable] before
/// reaching here, because they were never written by any build and are evidence
/// of damage rather than of a release.
final class HeldCopyOutdated extends HeldCopyState {
  const HeldCopyOutdated({required this.storedVersion, required this.readableVersion});

  /// The `schema_version` on disk. A payload field rather than a figure — safe
  /// to log, and the only field of the stored document this state exposes.
  ///
  /// **That safety is conditional and the condition is enforced upstream**: the
  /// store admits only a canonical positive decimal into this state, so the
  /// value is a short numeric string rather than whatever the document happened
  /// to carry. Constructing this state from an unvalidated field would turn a
  /// loggable fact back into a document-text channel.
  final String storedVersion;

  /// What this build reads: [PhonePayload.supportedSchemaVersion].
  final String readableVersion;
}
