import 'package:flutter/foundation.dart';

/// Whether this device's wall clock has run continuously since an anchor, and
/// by how much it disagrees with real elapsed time if it has not.
///
/// §9.1 rule 1's second branch asks whether the device clock moved backwards
/// since the last successful fetch. **That question cannot be answered by the
/// wall clock alone**, and the two arguments this app tried are both recorded
/// here because each looked sound and failed in the case the feature exists for:
///
/// 1. *"`last_fetch_attempt_at` is the latest reading this clock is known to
///    have produced, so a `device_now` earlier than it proves a regression."*
///    True, and it bounds the undetected residue by the time since that
///    attempt — which is **unbounded when there are no attempts**. Nine days
///    with the app closed, then a correction backwards landing anywhere after
///    that stamp, and a nine-day-old copy renders fresh.
/// 2. *"The host is a second clock, so a successful fetch corroborates ours."*
///    `published_at` is the host's clock **at publication, not at this
///    response**. A reachable host whose publisher has stopped serves an
///    arbitrarily old publication — which is precisely the `HOST_NOT_PUBLISHING`
///    case §9.1 exists to report, so the corroboration fails exactly where it
///    is needed.
///
/// The tell in both is the same: each found a quantity that behaves like
/// current time in the *healthy* case and never quantified it over the failure
/// case. So the evidence has to come from a clock that is not the wall clock —
/// a monotonic source that **counts suspended time**, since days spent asleep
/// still age a copy.
///
/// **This is fail-closed by construction.** There is no "assume continuous"
/// value: a source that cannot answer says [ContinuityUnknown], and the
/// predicate refuses to assert *either* freshness or host blame on it. An
/// unknown that renders as a confident verdict is the defect this type exists
/// to prevent, so the absence of evidence must not be spellable as evidence.
@immutable
sealed class ClockContinuity {
  const ClockContinuity();
}

/// Continuity could not be established, so nothing may be concluded from the
/// wall clock. Never "probably fine".
@immutable
final class ContinuityUnknown extends ClockContinuity {
  const ContinuityUnknown(this.gap);

  final ContinuityGap gap;

  @override
  bool operator ==(Object other) => other is ContinuityUnknown && other.gap == gap;

  @override
  int get hashCode => gap.hashCode;

  @override
  String toString() => 'ContinuityUnknown($gap)';
}

/// Why continuity is unknown. Each is a different thing to say to the owner,
/// and none of them is "the clock is wrong".
enum ContinuityGap {
  /// No anchor has been established yet under this pairing — the first run, or
  /// the first run after the record was lost.
  noAnchor,

  /// An anchor exists and could not be read.
  anchorUnreadable,

  /// The platform could not supply a monotonic reading at all.
  sourceUnavailable,

  /// An anchor exists but this reading cannot be proved to come from the same
  /// unbroken run of the monotonic source.
  ///
  /// **A reading merely being greater than the stored one is not proof of the
  /// same boot** — a monotonic counter that resets at boot passes that test
  /// after any uptime longer than the anchor's. The honest answer when a run
  /// cannot be identified is this one.
  discontinuous,
}

/// Continuity established: the same interval, measured two ways.
///
/// [wallElapsed] is `device_now` minus the anchor's wall-clock stamp.
/// [monotonicElapsed] is the same interval as the monotonic source counts it,
/// suspended time included. Under a clock that has not been adjusted these
/// agree; the difference is the adjustment.
@immutable
final class ContinuityHeld extends ClockContinuity {
  const ContinuityHeld({required this.wallElapsed, required this.monotonicElapsed});

  final Duration wallElapsed;
  final Duration monotonicElapsed;

  /// How far the wall clock has drifted from real elapsed time since the anchor.
  ///
  /// Negative when the clock was moved **backwards** — the case that renders an
  /// old copy fresh. Positive when it was moved forward, which ages a copy that
  /// is actually young.
  Duration get drift => wallElapsed - monotonicElapsed;

  /// Whether [drift] is small enough that the wall clock may be used.
  ///
  /// **Symmetric, deliberately.** A backwards jump is the dangerous direction
  /// and a forwards jump only over-reports staleness — but §9.1 rule 1 asks
  /// whether the clock can be trusted, not whether this particular error
  /// happens to be conservative. A phone that has been silently re-timed does
  /// not know the age of what it holds in either direction, and `COPY_UNKNOWN`
  /// is the answer for both.
  bool get isTrustworthy => drift.abs() <= tolerance;

  /// The allowance, and it is not a guess about clocks — it is the slop in the
  /// measurement itself. The two readings are taken at different points in the
  /// call, and a monotonic source that reports in whole seconds can be most of
  /// a second stale before it is read. Anything larger than this is an
  /// adjustment, not sampling noise. Deliberately far smaller than §9.1's
  /// `grace`, which is margin for *publication* cadence and has nothing to say
  /// about whether this device's clock is honest.
  static const Duration tolerance = Duration(seconds: 2);

  @override
  bool operator ==(Object other) =>
      other is ContinuityHeld &&
      other.wallElapsed == wallElapsed &&
      other.monotonicElapsed == monotonicElapsed;

  @override
  int get hashCode => Object.hash(wallElapsed, monotonicElapsed);

  @override
  String toString() =>
      'ContinuityHeld(wall: $wallElapsed, monotonic: $monotonicElapsed, drift: $drift)';
}

/// Everything about the clock that is observed **before** the wall clock is
/// read, with the reading itself left to [at].
///
/// This split exists to make an ordering hazard unrepresentable rather than
/// documented. Continuity is `device_now - anchor.anchoredAt` against the
/// monotonic delta, and `evaluateCopyState` compares the same `device_now`
/// against instants the diagnostics carry. If `device_now` is captured first
/// and a fetch then lands a *newer* anchor, the subtraction goes negative and
/// an ordinary race renders as a clock that moved backwards — the phone would
/// report a fault to the owner on the strength of its own argument order. The
/// predicate's doc comment states the rule for the records; this type enforces
/// it for the anchor, because the anchor read is the one that happens inside a
/// helper where a caller cannot see the order.
///
/// The residue is the gap between observing and reading, which is a few lines
/// of the same call: that is precisely what [ContinuityHeld.tolerance] is for,
/// and it is why that tolerance is sampling slop rather than a claim about
/// clocks.
sealed class ClockEvidence {
  const ClockEvidence();

  /// The verdict, given a wall-clock reading taken **after** this evidence.
  ClockContinuity at(DateTime deviceNow);
}

/// No usable evidence, and [gap] says which kind of nothing it was.
@immutable
final class NoClockEvidence extends ClockEvidence {
  const NoClockEvidence(this.gap);

  final ContinuityGap gap;

  @override
  ClockContinuity at(DateTime deviceNow) => ContinuityUnknown(gap);
}

/// An anchor and a comparable reading from the same run.
@immutable
final class AnchoredClockEvidence extends ClockEvidence {
  const AnchoredClockEvidence({
    required this.anchoredAt,
    required this.monotonicElapsed,
  });

  /// The wall clock's reading when the held copy arrived.
  final DateTime anchoredAt;

  /// Real time since then, as the monotonic source counts it.
  final Duration monotonicElapsed;

  @override
  ClockContinuity at(DateTime deviceNow) => ContinuityHeld(
        wallElapsed: deviceNow.difference(anchoredAt),
        monotonicElapsed: monotonicElapsed,
      );
}
