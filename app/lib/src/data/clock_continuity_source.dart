import '../domain/clock_anchor.dart';
import '../domain/clock_continuity.dart';
import 'clock_anchor_store.dart';

/// A clock that is not the wall clock, for `DESIGN.md` §9.1's second check.
///
/// Two obligations, and neither is negotiable:
///
/// 1. **It counts suspended time.** Days the device spent asleep still age the
///    copy on it, so a counter that stops while the device sleeps is not
///    measuring the thing being compared.
/// 2. **It names its run.** [MonotonicReading.runId] identifies one unbroken
///    run; a reading merely being larger than the stored one proves nothing,
///    since a counter that resets at boot exceeds any shorter previous uptime.
///    A source that cannot prove two readings come from the same run must mint
///    a new id and let the evidence be honestly lost.
///
/// `read` answers `null` when the platform cannot supply a reading at all —
/// which resolves to [ContinuityGap.sourceUnavailable] and therefore
/// `COPY_UNKNOWN`, never to an assumption of continuity.
abstract interface class ClockContinuitySource {
  /// A reading from this run, or `null` if the platform cannot supply one.
  Future<MonotonicReading?> read();
}

/// No monotonic source. **The only implementation this app ships today, and
/// that is a finding rather than an omission.**
///
/// The obvious convenience implementation is a process-lifetime `Stopwatch`
/// plus a random per-process run id. It would satisfy obligation 2 and compile,
/// and it must not be written, because it silently fails obligation 1 in
/// exactly the case this contract exists for. The argument is short enough to
/// check:
///
/// Let `T` be the real time since the anchor, `M` what the source reports, and
/// `W` what the wall clock reports. A source that stops while the device is
/// suspended has `M <= T`, so the drift this app computes is
/// `D = W - M >= W - T`, which is the true wall-clock drift. `D` is therefore
/// biased **upward**: it over-reports the forward direction, which only costs a
/// spurious `COPY_UNKNOWN`, and under-reports the backward direction, which is
/// the one that renders an old copy fresh. Concretely — suspended nine days
/// (`M ≈ 0`) with the clock moved back nine days (`W ≈ 0`) gives `D ≈ 0` and a
/// *trustworthy* verdict on a clock that is nine days wrong. That is this task's
/// original counterexample, reproduced by the shortcut meant to close it.
///
/// Whether Dart's `Stopwatch` counts suspended time is **not the question, and
/// is deliberately not asserted here**: both platforms expose the two clocks
/// separately precisely because the distinction is real (`elapsedRealtime()`
/// beside `uptimeMillis()`, `mach_continuous_time()` beside
/// `mach_absolute_time()`), and a source whose suspend semantics are unverified
/// cannot be used for a check whose failure mode is the confident wrong answer.
/// Verifying them means a real device across a real suspend, which is what task
/// `22` still owes along with the platform channel itself.
///
/// Until then this is what ships, and what it produces is honest: every copy
/// reads `COPY_UNKNOWN` — *"couldn't tell how old this is"* — rather than fresh.
class UnavailableClockContinuitySource implements ClockContinuitySource {
  const UnavailableClockContinuitySource();

  @override
  Future<MonotonicReading?> read() async => null;
}

/// Reads the anchor and a live monotonic reading, and reports what they prove.
///
/// The result is [ClockEvidence] rather than [ClockContinuity] because the wall
/// clock has not been read yet — see [ClockEvidence] for why that ordering is a
/// type here instead of a comment.
class ClockEvidenceReader {
  const ClockEvidenceReader({required this.anchors, required this.source});

  final ClockAnchorStore anchors;
  final ClockContinuitySource source;

  /// What this phone can prove about its clock since the held copy arrived.
  ///
  /// Every branch that is not a proof is a [ContinuityGap], and the caller
  /// cannot tell them apart from a claim of continuity by accident: there is no
  /// "assume continuous" value to fall into.
  Future<ClockEvidence> observe(String pairingId) async {
    // The anchor is read **before** the live reading, and the order is load
    // bearing. A fetch landing between the two would otherwise be read with a
    // new anchor against a reading taken before it, making the monotonic delta
    // negative — an ordinary race reported as a broken monotonic source.
    final ClockAnchor anchor;
    switch (await anchors.read(pairingId)) {
      case AnchorAbsent():
        return const NoClockEvidence(ContinuityGap.noAnchor);
      case AnchorUnreadable():
        return const NoClockEvidence(ContinuityGap.anchorUnreadable);
      case AnchorHeld(anchor: final held):
        anchor = held;
    }

    final MonotonicReading? now;
    try {
      now = await source.read();
    } on Object {
      // Caught as `Object`: a platform channel can raise types this layer has
      // never heard of, and one of them reaching a caller that is holding a
      // fail-closed contract would be the one way to get an uncaught throw out
      // of a function whose whole purpose is to have an answer for everything.
      return const NoClockEvidence(ContinuityGap.sourceUnavailable);
    }
    if (now == null) {
      return const NoClockEvidence(ContinuityGap.sourceUnavailable);
    }
    if (now.runId != anchor.reading.runId) {
      return const NoClockEvidence(ContinuityGap.discontinuous);
    }
    final elapsed = now.elapsed - anchor.reading.elapsed;
    if (elapsed.isNegative) {
      // Same run id, smaller reading. One of the two is wrong and there is no
      // way to tell which, so the run identity has stopped meaning anything —
      // which is [ContinuityGap.discontinuous], not a drift measurement taken
      // from a counter that just went backwards.
      return const NoClockEvidence(ContinuityGap.discontinuous);
    }
    return AnchoredClockEvidence(
      anchoredAt: anchor.anchoredAt,
      monotonicElapsed: elapsed,
    );
  }
}
