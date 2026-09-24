import 'package:flutter/foundation.dart';

import 'payload_format_exception.dart';
import 'publication_seq.dart';

/// A reading from a clock that is not the wall clock.
///
/// Two fields, and the second one is the whole reason this is a type rather
/// than a [Duration]. `DESIGN.md` §9.1's mechanism is "compare elapsed wall
/// time against elapsed monotonic time", which is only meaningful when both
/// readings come from **the same unbroken run** of the same counter. A counter
/// that resets — at reboot, or at process start for a process-scoped
/// implementation — produces a second reading that is perfectly valid and
/// entirely incomparable with the first.
///
/// **A reading merely being greater than the stored one is not proof of the
/// same run**, and that is not a hypothetical: `SystemClock.elapsedRealtime()`
/// resets at boot, so any uptime longer than the anchor's passes that test. So
/// the source states its run identity and the store compares it literally; a
/// source that cannot prove continuity across a restart mints a new [runId] and
/// the evidence is honestly lost. Inventing a portable boot identity to avoid
/// that answer is explicitly not allowed here.
@immutable
class MonotonicReading {
  const MonotonicReading({required this.runId, required this.elapsed});

  /// Identifies one unbroken run of the source. Opaque: only equality is ever
  /// asked of it, and no ordering between two runs is defined or defensible.
  final String runId;

  /// How long this run has been going, **counting suspended time** — days spent
  /// asleep still age a copy, so a source that stops while the device sleeps
  /// cannot answer §9.1's question (see `ClockContinuitySource`).
  final Duration elapsed;

  @override
  bool operator ==(Object other) =>
      other is MonotonicReading && other.runId == runId && other.elapsed == elapsed;

  @override
  int get hashCode => Object.hash(runId, elapsed);

  @override
  String toString() => 'MonotonicReading($runId, $elapsed)';
}

/// Whether anything has ever corroborated the wall-clock reading an anchor was
/// stamped with.
///
/// **Continuity and correctness are two different claims, and conflating them
/// is how a nine-day-old copy renders fresh.** [ContinuityHeld] proves the wall
/// clock has not been *adjusted* since the anchor. §9.1's comparison needs more
/// than that: `device_now` is measured against `stale_after`, which is built
/// from the host's `published_at`. That comparison is only meaningful if this
/// device's clock agrees with the host's — and an unbroken run since an
/// arbitrary starting reading says nothing whatever about whether that reading
/// was right.
///
/// Measured, not argued: a phone whose clock is nine days slow, anchoring a
/// publication it has just received, reports zero drift a moment later and
/// renders a nine-day-old copy `COPY_FRESH`. Zero drift since an unproven
/// reading is zero evidence.
enum AnchorTrust {
  /// The wall clock agreed with the host's at the moment this anchor was
  /// stamped, and something proved it.
  ///
  /// **No caller in this app can construct this today**, and that is the state
  /// of the work rather than an oversight. A single fetch cannot supply the
  /// proof: `published_at` is the host's clock *at publication*, so a reachable
  /// host whose publisher has stopped serves an arbitrarily old instant — which
  /// is precisely the `HOST_NOT_PUBLISHING` case, so the corroboration fails
  /// exactly where it is needed. The future-publication check is one-sided
  /// evidence of *disagreement* and never corroboration of agreement.
  ///
  /// The proof that will be able to set this is the transport's, not this
  /// layer's: two successful fetches from the same monotonic run, the second
  /// returning a higher `seq` than the first. The host serves its latest
  /// publication, so a `seq` absent from the earlier reply was created *between*
  /// the two — which bounds the copy's age by a monotonically measured interval
  /// and never consults the wall clock at all. Owed by task `22` along with the
  /// transport itself.
  corroborated,

  /// Nothing corroborates it. **The only value this app constructs today**, and
  /// therefore the reason every copy reads `COPY_UNKNOWN`.
  ///
  /// Fail-closed is the whole point: there is no third value meaning "probably
  /// fine", and an anchor cannot become [corroborated] by being re-taken.
  unproven,
}

/// The moment the copy this phone holds was received, stamped by both clocks.
///
/// **The anchor is not "the last time we looked at the clock" — it is when the
/// held copy arrived**, and that distinction is the whole design. The predicate
/// asks how old the *copy* is, so the wall clock must be shown to have run
/// continuously since the copy was taken. An anchor re-taken at some later,
/// arbitrary moment would grant trust over an interval shorter than the copy's
/// real age, which is exactly how a nine-day-old copy renders fresh.
///
/// That is why [seq] is here: it names the publication the anchor vouches for,
/// and `ClockAnchorStore.establish` refuses an anchor that does not advance it.
/// A fetch returning the publication we already hold changes nothing about when
/// that copy arrived, so it must not move the anchor.
///
/// [trust] is the second half of that guard and it was missing. Refusing to move
/// the anchor backwards stops one hole; an *advance* opened the same one, by
/// replacing a record that had already measured a rolled-back clock with a fresh
/// pair of readings that trivially agree. See [AnchorTrust].
@immutable
class ClockAnchor {
  ClockAnchor({
    required this.pairingId,
    required this.anchoredAt,
    required this.reading,
    required this.seq,
    required this.trust,
  }) {
    if (pairingId.isEmpty) {
      throw const PayloadFormatException('clock anchor has no pairing_id');
    }
    if (reading.elapsed.isNegative) {
      throw const PayloadFormatException('clock anchor has a negative monotonic reading');
    }
    if (reading.runId.isEmpty) {
      throw const PayloadFormatException('clock anchor has no run_id');
    }
  }

  /// The pairing this anchor was taken under. Scoped for the same reason
  /// [FetchDiagnostics] is: an anchor from a previous pairing is a reading of a
  /// counter this pairing never saw.
  final String pairingId;

  /// The wall clock's own reading when the copy arrived. Untrusted by itself —
  /// it is one half of the comparison, not the answer.
  final DateTime anchoredAt;

  /// The monotonic reading taken at that same moment.
  final MonotonicReading reading;

  /// The publication this anchor vouches for.
  final PublicationSeq seq;

  /// What [anchoredAt] is worth. Decided by `ClockAnchorStore.establish` on the
  /// way in, never by whoever happens to be holding the value.
  final AnchorTrust trust;

  @override
  bool operator ==(Object other) =>
      other is ClockAnchor &&
      other.pairingId == pairingId &&
      other.anchoredAt.isAtSameMomentAs(anchoredAt) &&
      other.reading == reading &&
      other.seq == seq &&
      other.trust == trust;

  @override
  int get hashCode => Object.hash(pairingId, anchoredAt.toUtc(), reading, seq, trust);
}

/// What the anchor store found — three outcomes, the same shape as
/// [BaselineState] and [DiagnosticsState] and for a third distinct reason.
///
/// Here *absent* and *unreadable* lead to the **same** verdict: both are
/// [ContinuityGap] values and both produce `COPY_UNKNOWN`, because the absence
/// of clock evidence is never evidence of a good clock. So this type is not
/// keeping a bypass shut — it is keeping two different sentences for the owner
/// apart. *"This phone has not established a clock anchor yet"* and *"this
/// phone had one and cannot read it"* differ in whether anything is broken, and
/// collapsing them would report a damaged record as an ordinary first run.
sealed class AnchorState {
  const AnchorState();
}

/// No anchor for this pairing — the first run, or the first after re-pairing.
final class AnchorAbsent extends AnchorState {
  const AnchorAbsent();
}

/// An anchor for this pairing, read back intact.
final class AnchorHeld extends AnchorState {
  const AnchorHeld(this.anchor);

  final ClockAnchor anchor;
}

/// A record exists and could not be read. Never *"no anchor yet"*.
final class AnchorUnreadable extends AnchorState {
  const AnchorUnreadable(this.reason);

  final String reason;
}
