import 'clock_continuity.dart';
import 'fetch_diagnostics.dart';
import 'seq_baseline.dart';

/// How old *this phone's copy* of the snapshot is — `DESIGN.md` §9.1.
///
/// This is the second of invariant **I4**'s two dimensions, and it is a
/// different question from how old the institutions' data is. A payload can say
/// `age_state: KNOWN, as_of: today` and still be a copy the phone fetched a week
/// ago, with six newer publications it never saw. Rendering only the first
/// dimension would print today's date over week-old money — which is the
/// original lie, reproduced inside the app that exists to refuse it.
///
/// So the two dimensions are separate types, evaluated separately, and rendered
/// as separate lines. They are never reduced to one badge.
enum CopyFreshness {
  /// The copy is within `publish_interval + grace` of its publication.
  fresh,

  /// The copy is past its deadline. §9.1 splits this by *reason*
  /// (`HOST_NOT_PUBLISHING` vs `CANNOT_CHECK`) using the device's own fetch
  /// history, which is why the reason lives on [CopyStale] and not here: this
  /// enum is the indicator, and an indicator that carried the reason would be
  /// the single merged badge §9.2 forbids. A caller holding only this value is
  /// holding the dimension on purpose.
  stale,

  /// The device clock and the payload disagree, so no age can be computed.
  unknown,
}

/// The deadline that travels with the payload, per §9.1.
///
/// `stale_after = published_at + publish_interval_seconds + grace_seconds`. The
/// phone never hardcodes the daemon's cadence — both numbers come off the wire
/// precisely so the promise can change without shipping a new APK.
DateTime staleAfter({
  required DateTime publishedAt,
  required Duration publishInterval,
  required Duration grace,
}) =>
    publishedAt.add(publishInterval).add(grace);

/// The dimension, evaluated — §9.1's answer together with why it says so.
///
/// [CopyFreshness] alone is what the badge switches on; a `stale` badge with no
/// reason beside it is the thing §9.1 was rewritten to prevent, because the two
/// reasons *"the host stopped publishing"* and *"this phone could not check"*
/// have different fixes and only one of them is anybody's fault. Keeping the
/// reason inside the answer means no caller can hold one without the other.
sealed class CopyState {
  const CopyState();

  /// The dimension itself, for the caller that only renders the indicator.
  CopyFreshness get freshness;
}

/// `COPY_FRESH` — within `publish_interval + grace` of publication.
final class CopyFresh extends CopyState {
  const CopyFresh();

  @override
  CopyFreshness get freshness => CopyFreshness.fresh;
}

/// `COPY_STALE`, with the reason §9.1 makes a predicate rather than a guess.
final class CopyStale extends CopyState {
  const CopyStale(this.reason);

  final StaleReason reason;

  @override
  CopyFreshness get freshness => CopyFreshness.stale;
}

/// `COPY_UNKNOWN` — the clocks disagree, so no age can be computed.
final class CopyUnknown extends CopyState {
  const CopyUnknown(this.disagreement);

  final ClockDisagreement disagreement;

  @override
  CopyFreshness get freshness => CopyFreshness.unknown;
}

/// Which of §9.1 rule 1's two clock checks fired.
///
/// They are one outcome and two sentences: one says the *server's* clock and
/// this device's disagree, the other says this device disagrees with **itself**,
/// and only the second one is fixed on this phone.
enum ClockDisagreement {
  /// `published_at > device_now + 5min`.
  payloadFromTheFuture,

  /// `device_now` is earlier than an instant this device itself stamped, or
  /// the wall clock has drifted from real elapsed time since the anchor.
  deviceClockMovedBackwards,

  /// **Whether the clock can be trusted is itself unknown** — no anchor, an
  /// unreadable one, no monotonic source, or a reading that cannot be proved to
  /// come from the same unbroken run. Distinct from
  /// [deviceClockMovedBackwards]: that one is a detected fault and this one is
  /// the absence of evidence, and telling the owner they are the same thing
  /// would be the confident answer §9.1 rule 1 forbids.
  clockContinuityUnknown,
}

/// Why the copy is stale — §9.1's two reasons, which have different fixes.
sealed class StaleReason {
  const StaleReason();
}

/// *"Reached the source; nothing has been published since <time>."*
///
/// **This is how a host-side failure reaches the owner at all** (task `22`): the
/// *publication overdue* alert cannot travel over the channel whose failure it
/// reports. Both fields are in the claim it makes — as of [confirmedAt] the host
/// had published nothing newer than [lastPublishedAt] — and a claim about the
/// host is only worth as much as the instant it was last confirmed.
final class HostNotPublishing extends StaleReason {
  const HostNotPublishing({required this.lastPublishedAt, required this.confirmedAt});

  /// `published_at` of the copy held — the newest publication this phone knows of.
  final DateTime lastPublishedAt;

  /// `last_fetch_success_at` — when the source last answered with that same copy.
  final DateTime confirmedAt;
}

/// *"Couldn't check since <time>"*, plus which inability it was.
///
/// The phone says nothing about the host here, because it has not reached it.
final class CannotCheck extends StaleReason {
  const CannotCheck({required this.cause, this.since});

  final CannotCheckCause cause;

  /// `last_fetch_success_at`, or `null` when no fetch has ever succeeded under
  /// this pairing — *"since"* needs an instant and inventing one would be the
  /// same lie one layer down.
  final DateTime? since;
}

/// What stopped the phone from checking — one sentence for the owner each, and
/// a case earns its place only by being a different thing for him to do.
sealed class CannotCheckCause {
  const CannotCheckCause();
}

/// §9.1's *"never fetched"*. Nothing to do: no fetch has been attempted under
/// this pairing yet.
final class NeverFetched extends CannotCheckCause {
  const NeverFetched();
}

/// A fetch was attempted and failed; [errorClass] is §9.1's error class, which
/// is the whole point of carrying it — *offline* is ordinary and
/// self-correcting, *host unreachable* is what a lost VPS looks like from here.
final class FetchFailed extends CannotCheckCause {
  const FetchFailed(this.errorClass);

  final FetchFailureClass errorClass;
}

/// Fetching is fine; the last success simply predates the copy's own deadline,
/// so the phone has not asked since there was anything to ask about. Nothing is
/// wrong yet and nobody is blamed.
final class NotCheckedSinceDue extends CannotCheckCause {
  const NotCheckedSinceDue();
}

/// The phone's own notes are missing or damaged, so it cannot say why.
///
/// Deliberately **not** [NeverFetched]: telling the owner a phone has never
/// fetched when it has fetched for a month and lost its notes is a claim with
/// nothing behind it, and the stores are `sealed` in three cases precisely so
/// this branch has to be written rather than defaulted into the other one.
final class RecordsUnusable extends CannotCheckCause {
  const RecordsUnusable(this.reason);

  final String reason;
}

/// The last payload the host served is not the one this phone holds, so the
/// third conjunct cannot say *"it had nothing newer than we hold"*.
///
/// That divergence has one cause on this design — a fetch the phone **refused**
/// under I6 — so the honest surface is the downgrade warning §9.3 keeps beside
/// it, not an accusation aimed at the host.
final class ServedPayloadNotHeld extends CannotCheckCause {
  const ServedPayloadNotHeld();
}

/// Evaluate §9.1's ordered rules over everything the phone can observe.
///
/// [diagnostics] and [baseline] carry the five persisted facts; both are the
/// `sealed` three-case states rather than nullable records, so *missing* and
/// *unreadable* cannot silently satisfy a comparison that a caller expected to
/// fail.
///
/// **Read [deviceNow] after reading the records, not before.** The backwards-
/// clock branch below compares it against an instant those records carry, so a
/// `deviceNow` captured before a fetch that then stamps its attempt is
/// indistinguishable from a clock that went back — the caller would report a
/// skewed clock to the owner on the strength of its own argument order.
CopyState evaluateCopyState({
  required DateTime publishedAt,
  required Duration publishInterval,
  required Duration grace,
  required DateTime deviceNow,
  required DiagnosticsState diagnostics,
  required BaselineState baseline,
  required ClockContinuity continuity,
}) {
  // Rule 1, both branches, before any age is computed.
  const tolerance = Duration(minutes: 5);
  if (publishedAt.isAfter(deviceNow.add(tolerance))) {
    return const CopyUnknown(ClockDisagreement.payloadFromTheFuture);
  }
  // **Clock trust, before any age is computed from the wall clock.**
  //
  // An earlier version of this predicate argued that no monotonic source was
  // needed: `last_fetch_attempt_at` is the latest reading this device's clock
  // is known to have produced, so a `device_now` earlier than it proves a
  // regression, and the undetected residue is bounded by the time since that
  // attempt. **The bound is unbounded when there are no attempts** — nine days
  // with the app closed, a correction landing anywhere after that stamp, and a
  // nine-day-old copy rendered fresh. [ClockContinuity] carries the two
  // arguments this app tried and why each failed in the case §9.1 exists for.
  //
  // So the order here is load-bearing: **an untrustworthy clock blocks host
  // blame as well as freshness.** `HOST_NOT_PUBLISHING` accuses the host of
  // having published nothing since a deadline this device computed, so a
  // predicate that skipped this check for the stale branch would make exactly
  // the misattribution §9.1 names, with more confidence rather than less.
  switch (continuity) {
    case ContinuityUnknown():
      return const CopyUnknown(ClockDisagreement.clockContinuityUnknown);
    case ContinuityHeld(isTrustworthy: false):
      return const CopyUnknown(ClockDisagreement.deviceClockMovedBackwards);
    case ContinuityHeld():
      break;
  }
  if (diagnostics case DiagnosticsHeld(diagnostics: final held)) {
    // Kept alongside the anchor rather than replaced by it. This one is two of
    // *this device's own stored readings* disagreeing, so it survives a restart
    // that the anchor may not, and it holds even where the anchor's run cannot
    // be identified. It is strictly weaker — it sees only regressions that
    // cross a stamp — which is why it is second and not instead.
    if (deviceNow.isBefore(held.lastAttemptAt) || held.clockMovedBackwards) {
      return const CopyUnknown(ClockDisagreement.deviceClockMovedBackwards);
    }
  }
  final deadline = staleAfter(
    publishedAt: publishedAt,
    publishInterval: publishInterval,
    grace: grace,
  );
  // §9.1 rule 2 is `device_now <= stale_after`, so the boundary instant itself
  // is still fresh.
  if (!deviceNow.isAfter(deadline)) {
    return const CopyFresh();
  }
  return CopyStale(
    _staleReason(
      publishedAt: publishedAt,
      deadline: deadline,
      diagnostics: diagnostics,
      baseline: baseline,
    ),
  );
}

/// §9.1 rule 3: `HOST_NOT_PUBLISHING` **iff all three** conjuncts hold.
StaleReason _staleReason({
  required DateTime publishedAt,
  required DateTime deadline,
  required DiagnosticsState diagnostics,
  required BaselineState baseline,
}) {
  final FetchDiagnostics held;
  switch (diagnostics) {
    case DiagnosticsAbsent():
      return const CannotCheck(cause: NeverFetched());
    case DiagnosticsUnreadable(:final reason):
      return CannotCheck(cause: RecordsUnusable(reason));
    case DiagnosticsHeld(diagnostics: final record):
      held = record;
  }

  // Conjunct 2 — `last_fetch_attempt_at == last_fetch_success_at`, *"and
  // nothing has failed since"* — is read as the record's shape rather than as
  // that comparison, and the difference is not cosmetic. [FetchDiagnostics]
  // makes the two instants **one value** on the success path and refuses to
  // record a failure at or before the last success, so no record exists in
  // which they are equal while an error is held. Writing the equality out would
  // be an expression that cannot be false — a guard nothing can pin, which is
  // the shape this project keeps finding in its own green suites.
  if (held.lastError case final FetchFailureClass error) {
    return CannotCheck(cause: FetchFailed(error), since: held.lastSuccess?.at);
  }
  // `lastError == null` is only reachable through `FetchDiagnostics.succeeded`,
  // which sets both.
  final success = held.lastSuccess!;

  // Conjunct 1 — we reached the source **after** the copy was already due, not
  // merely recently. Before that instant the source had nothing to report yet.
  if (success.at.isBefore(deadline)) {
    return CannotCheck(cause: const NotCheckedSinceDue(), since: success.at);
  }

  // Conjunct 3 — `last_fetch_seq == last_seq`: what it served us last is what
  // we hold. The baseline is the only place `last_seq` lives, so its two
  // non-value cases are answers here, not defaults: a phone holding a payload
  // with no readable baseline cannot establish this conjunct at all.
  switch (baseline) {
    case BaselineAbsent():
      return CannotCheck(
        cause: const RecordsUnusable('this phone holds a copy but no I6 baseline for its pairing'),
        since: success.at,
      );
    case BaselineUnreadable(:final reason):
      return CannotCheck(cause: RecordsUnusable(reason), since: success.at);
    case BaselineHeld(:final baseline):
      if (baseline.lastSeq != success.seq) {
        return CannotCheck(cause: const ServedPayloadNotHeld(), since: success.at);
      }
  }

  return HostNotPublishing(lastPublishedAt: publishedAt, confirmedAt: success.at);
}

/// The dimension alone, for callers that do not yet read the phone's own
/// history — today that is the fixture-backed build's payload accessor.
///
/// It delegates rather than repeating rules 1 and 2, so the two answers cannot
/// drift; a build with no stored history is [DiagnosticsAbsent] by definition,
/// which is §9.1's *"never fetched"* and nothing else.
CopyFreshness evaluateCopyFreshness({
  required DateTime publishedAt,
  required Duration publishInterval,
  required Duration grace,
  required DateTime deviceNow,
  required ClockContinuity continuity,
}) =>
    evaluateCopyState(
      publishedAt: publishedAt,
      publishInterval: publishInterval,
      grace: grace,
      deviceNow: deviceNow,
      continuity: continuity,
      diagnostics: const DiagnosticsAbsent(),
      baseline: const BaselineAbsent(),
    ).freshness;

/// The connection dimension, evaluated by the daemon and carried on the wire.
///
/// §9.2 is explicit that the phone never re-implements this policy — it arrives
/// already decided, split so that the "no owner action" states can never be
/// rendered as a demand to re-link.
enum ConnectionDisplayState {
  ok,
  waiting,
  actionNeeded;

  static ConnectionDisplayState fromWire(String value) => switch (value) {
        'OK' => ConnectionDisplayState.ok,
        'WAITING' => ConnectionDisplayState.waiting,
        'ACTION_NEEDED' => ConnectionDisplayState.actionNeeded,
        _ => throw ArgumentError.value(value, 'connection_state'),
      };
}
