import 'package:flutter/foundation.dart';

import 'payload_format_exception.dart';
import 'publication_seq.dart';

/// Why a fetch attempt failed, as a class rather than a message.
///
/// `DESIGN.md` §9.1 requires this and says why in a sentence worth keeping: a
/// dead host and a phone that has dropped off the tailnet both land in
/// `CANNOT_CHECK`, and with alerting reduced to the one in-app channel (§11)
/// those two *"sit at opposite ends of how much the owner should care"*.
///
/// Each value here is a different answer to *what should he do about it*, which
/// is the test for whether a class earns its place — a class that maps to the
/// same action as its neighbour is a message, not a class, and belongs in the
/// text beside it.
enum FetchFailureClass {
  /// No network at all. Ordinary and self-correcting; the owner does nothing.
  offline,

  /// The network is fine and the host did not answer. *"The shape a lost VPS
  /// takes on the only screen that can report it."*
  hostUnreachable,

  /// Reached it and it refused us. The pairing is what is wrong, so this is the
  /// one class whose fix is re-pairing.
  credentialRejected,

  /// Reached it, it answered, and the answer was unusable — TLS failure, a 5xx,
  /// a body that would not parse. Something is broken, but not simply gone.
  transportError,

  /// The attempt failed and **this phone cannot say where**.
  ///
  /// The fifth class, and it is here because the other four each make a claim.
  /// `SnapshotTransportFault.unclassified` exists for the opposite reason —
  /// *"deliberately not folded into the nearest neighbour ... guessing one it
  /// cannot [defend] would put invented evidence into the one channel §11 leaves
  /// for host-side failure"* — and the adapter that maps the transport's faults
  /// onto these classes is the first code that has to place it. There was
  /// nowhere honest: [transportError]'s copy is *"your server answered with
  /// something this app couldn't use"*, which tells the owner to go look at a
  /// server that may never have been reached, and [offline] and [hostUnreachable]
  /// each assert the half of the network the phone has no evidence about.
  ///
  /// It passes the test above rather than being an escape hatch: *what should he
  /// do about it* is **wait and look again**, which is not what any of the other
  /// four say. An unnamed fault is usually transient, and the one action it must
  /// not produce is re-pairing a host that is fine.
  unknownFailure,
}

/// The last fetch that actually returned a payload.
///
/// The instant and the `seq` are one value rather than two nullable fields
/// because §9.1's facts contain a both-or-neither pair: `last_fetch_seq` is
/// *"the `seq` the last successful fetch returned"*, so it exists exactly when
/// that fetch does. Separate nullables would make "succeeded but returned no
/// `seq`" representable, and the predicate's third conjunct
/// (`last_fetch_seq == last_seq`) would then have to invent an answer for it.
@immutable
class FetchSuccess {
  const FetchSuccess({required this.at, required this.seq});

  /// By the device's own clock, which is the only clock an attempt has.
  final DateTime at;

  /// The `seq` that fetch returned — not necessarily the one the phone holds.
  /// They differ exactly when a fetch was refused, which is what makes the two
  /// separate facts in §9.1's list.
  final PublicationSeq seq;

  @override
  bool operator ==(Object other) =>
      other is FetchSuccess && other.at.isAtSameMomentAs(at) && other.seq == seq;

  @override
  int get hashCode => Object.hash(at.toUtc(), seq);
}

/// What this phone knows about its own fetching, under one pairing.
///
/// Five of §9.1's six facts live here; the sixth (`last_seq`, the `seq` the
/// phone actually *holds*) is I6's baseline and lives in [SeqBaselineStore].
///
/// **Scoped to a `pairing_id`, and that is not symmetry with the baseline —
/// the third conjunct forces it.** `HOST_NOT_PUBLISHING` requires
/// `last_fetch_seq == last_seq`, and `last_seq` is per-pairing by §9.3. A
/// `last_fetch_seq` left over from a previous pairing is a reading of a
/// different counter: the daemon allocates `max(seq) + 1` within its own
/// database, so two pairings' counters are unrelated numbers that are entirely
/// free to coincide. Comparing them would produce `HOST_NOT_PUBLISHING` —
/// *"reached the source; nothing has been published since"* — about a host that
/// is publishing perfectly well, which is the exact misattribution §9.1 says
/// this state exists to prevent. The other three facts point the same way: a
/// `credentialRejected` carried across a re-pairing would tell the owner to fix
/// the thing he just fixed.
@immutable
class FetchDiagnostics {
  /// The last attempt succeeded, so it *is* the last success. The two instants
  /// coincide by construction rather than by agreement between two arguments.
  FetchDiagnostics.succeeded({
    required this.pairingId,
    required DateTime at,
    required PublicationSeq seq,
  })  : lastAttemptAt = at,
        lastError = null,
        lastSuccess = FetchSuccess(at: at, seq: seq),
        foundNoPublication = false {
    _checkPairing(pairingId);
  }

  /// The last attempt failed. [after] is the last success before it, if any —
  /// `null` on a phone that has attempted under this pairing and never
  /// succeeded, which is a different state from having never attempted at all.
  FetchDiagnostics.failed({
    required this.pairingId,
    required DateTime at,
    required FetchFailureClass error,
    FetchSuccess? after,
  })  : lastAttemptAt = at,
        lastError = error,
        lastSuccess = after,
        foundNoPublication = false {
    _checkPairing(pairingId);
  }

  /// The last attempt **reached the host and the host had nothing to serve** —
  /// `serve.py`'s `404`, which [SnapshotNoPublication] keeps as its own outcome
  /// rather than an error.
  ///
  /// **The third state, and this record could not say it.** `snapshot_reader.dart`
  /// deferred the question here in as many words: the adapter lands *"in the
  /// change that also decides where `HOST_NOT_PUBLISHING` sits in
  /// `FetchDiagnostics`"*. Neither existing constructor can take it.
  /// [FetchDiagnostics.failed] is the one the transport already argues against —
  /// it breaks the second conjunct, drops the reason to `CANNOT_CHECK`, and
  /// reports a publisher that has stopped as a network problem, which is the one
  /// fault this app exists to surface, misattributed. And
  /// [FetchDiagnostics.succeeded] cannot take it either: it requires a `seq`, a
  /// `404` returns none, and [FetchSuccess] bundles the instant with the `seq`
  /// **precisely** so that "succeeded but returned no `seq`" is not a shape this
  /// type can hold.
  ///
  /// [after] is the last success before it, exactly as on [FetchDiagnostics.failed]:
  /// a host that served a copy and later has none is the interesting case, and
  /// the copy it served is still the one the phone is showing.
  FetchDiagnostics.foundNoPublication({
    required this.pairingId,
    required DateTime at,
    FetchSuccess? after,
  })  : lastAttemptAt = at,
        lastError = null,
        lastSuccess = after,
        foundNoPublication = true {
    _checkPairing(pairingId);
  }

  /// The pairing every fact below was observed under.
  final String pairingId;

  /// `last_fetch_attempt_at` — when this phone last tried, success or not.
  final DateTime lastAttemptAt;

  /// `last_fetch_error` — the class of the failure, `null` when the last
  /// attempt did not fail. Not a history: it explains the phone's *current*
  /// inability, and a success ends that.
  ///
  /// `null` no longer means *succeeded*: [foundNoPublication] is the other way
  /// to reach it, and a reader that treats the two as one is reading a `404` as
  /// a returned payload.
  final FetchFailureClass? lastError;

  /// Whether the last attempt reached the host and found no publication.
  ///
  /// **Set by the constructor and derivable from nothing else**, which is the
  /// answer to the obvious objection that a `bool` beside a nullable is the
  /// flag shape this file refuses elsewhere. The objection is about fields that
  /// can be set independently and then disagree; there is no path here that
  /// sets this one, so the three last-attempt states are as disjoint as a
  /// `sealed` hierarchy would make them. What a hierarchy would add is
  /// exhaustiveness on the reader's side, and it would cost restructuring
  /// [lastError] and [lastSuccess] — two fields whose exact shape the §9.1
  /// predicate and this record's on-disk format were both argued against.
  /// [DiagnosticsState] already forces the branch that matters.
  final bool foundNoPublication;

  /// `last_fetch_success_at` + `last_fetch_seq`, or `null` if no fetch under
  /// this pairing has ever returned a payload.
  final FetchSuccess? lastSuccess;

  /// This record's own proof that the device clock moved backwards.
  ///
  /// A failed attempt stamped at or before the success it followed can only
  /// happen one way: the two stamps came from a clock that was corrected
  /// backwards between them. **Attempt order is not wall-clock order**, and an
  /// earlier version of this class refused to record such a failure at all —
  /// which meant that after a backwards correction the phone kept the older
  /// *success* on disk, and §9.1's second conjunct, reading no held error, went
  /// on to report `HOST_NOT_PUBLISHING` about a host it had just failed to
  /// reach. Refusing looked like the conservative direction and was the opposite
  /// of it: the refusal discarded the newer, worse news.
  ///
  /// Nothing about the second conjunct needed the ordering. It is read from
  /// [lastError] — the outcome the attempt actually had — and never from a
  /// comparison of the two instants, so recording the failure is all that was
  /// ever required. What the ordering is good for is this: it is the only
  /// evidence of a backwards correction that survives a restart, since it is
  /// two of this device's own readings disagreeing rather than a claim about
  /// now. It does **not** bound how far back the clock went, and it cannot see a
  /// correction applied while no attempt was made at all.
  bool get clockMovedBackwards {
    final success = lastSuccess;
    return returnedNoPayload && success != null && !success.at.isBefore(lastAttemptAt);
  }

  /// Whether the last attempt came back without a payload.
  ///
  /// The union of the two non-success states, named once because both readers
  /// of it want the union and neither wants to enumerate: what makes the
  /// ordering above evidence is that [lastSuccess] is *not* the last attempt,
  /// and what makes conjunct 2 of `HOST_NOT_PUBLISHING` fail is the same fact.
  /// Written as `lastError != null` before the `404` had a state, this getter
  /// is where that reading is corrected in one place rather than at each site.
  bool get returnedNoPayload => lastError != null || foundNoPublication;

  static void _checkPairing(String pairingId) {
    if (pairingId.isEmpty) {
      throw const PayloadFormatException('fetch diagnostics have no pairing_id');
    }
  }

  @override
  bool operator ==(Object other) =>
      other is FetchDiagnostics &&
      other.pairingId == pairingId &&
      other.lastAttemptAt.isAtSameMomentAs(lastAttemptAt) &&
      other.lastError == lastError &&
      other.foundNoPublication == foundNoPublication &&
      other.lastSuccess == lastSuccess;

  @override
  int get hashCode => Object.hash(
        pairingId,
        lastAttemptAt.toUtc(),
        lastError,
        foundNoPublication,
        lastSuccess,
      );
}

/// What the store found for a pairing — **three** outcomes, not two.
///
/// The reason is not the one [BaselineState] has, and the difference is worth
/// stating so this type is not read as inheriting a security argument it does
/// not have. Damaging *this* file cannot bypass a check: without the facts it
/// holds no conjunct of `HOST_NOT_PUBLISHING` can be established, so the
/// predicate falls to `CANNOT_CHECK` and the phone accuses nobody.
///
/// It exists for the other half of the same requirement — **a missing fact must
/// never be readable as a satisfied one.** §9.1 ends `CANNOT_CHECK` with an
/// error class, and *"never fetched"* is one of them; telling the owner that on
/// a phone that has fetched for a month and lost its notes is a claim with
/// nothing behind it. A nullable record invites exactly that, because the
/// predicate's conjuncts are comparisons and `null == null` is `true`: a damaged
/// file and a fresh install would both offer an absent `last_fetch_seq` that
/// compares equal to an absent `last_seq`, and the conjunct a caller expected to
/// fail would hold. A `sealed` type makes the caller write the branch instead.
sealed class DiagnosticsState {
  const DiagnosticsState();
}

/// No fetch has ever been attempted under this pairing. §9.1's *"never
/// fetched"*, and deliberately **not** a [FetchFailureClass] value: the record's
/// own existence carries that fact, and a second spelling of it could disagree
/// with the first.
final class DiagnosticsAbsent extends DiagnosticsState {
  const DiagnosticsAbsent();
}

/// Diagnostics for this pairing, read back intact.
final class DiagnosticsHeld extends DiagnosticsState {
  const DiagnosticsHeld(this.diagnostics);

  final FetchDiagnostics diagnostics;
}

/// A record exists and could not be read. Never *"never fetched"*.
final class DiagnosticsUnreadable extends DiagnosticsState {
  const DiagnosticsUnreadable(this.reason);

  /// What was wrong, for the screen. Carries no stored bytes.
  final String reason;
}
