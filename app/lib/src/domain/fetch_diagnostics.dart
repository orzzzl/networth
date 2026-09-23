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
}

/// The last fetch that actually returned a payload.
///
/// The instant and the `seq` are one value rather than two nullable fields
/// because §9.1's five facts contain a both-or-neither pair: `last_fetch_seq` is
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
/// Four of §9.1's five facts live here; the fifth (`last_seq`, the `seq` the
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
        lastSuccess = FetchSuccess(at: at, seq: seq) {
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
        lastSuccess = after {
    _checkPairing(pairingId);
    if (after != null && !after.at.isBefore(at)) {
      // **The one state §9.1's second conjunct cannot describe honestly.** It
      // reads `last_fetch_attempt_at == last_fetch_success_at` as "and nothing
      // has failed since", so a failure recorded at or before the instant of the
      // last success makes that conjunct true while an error is held — the
      // phone would announce `HOST_NOT_PUBLISHING` on the strength of a fetch it
      // knows failed. Refusing is the conservative direction: the record keeps
      // its previous contents and the predicate falls to `CANNOT_CHECK`, which
      // blames nobody. Unreachable from the transport, where attempts are
      // sequential and a round trip cannot complete inside one microsecond, so
      // this guards the bytes on disk and a future caller, not today's.
      throw PayloadFormatException(
        'a failed attempt at $at cannot be recorded at or before the last '
        'success at ${after.at}',
      );
    }
  }

  /// The pairing every fact below was observed under.
  final String pairingId;

  /// `last_fetch_attempt_at` — when this phone last tried, success or not.
  final DateTime lastAttemptAt;

  /// `last_fetch_error` — the class of the failure, `null` **iff** the last
  /// attempt succeeded. Not a history: it explains the phone's *current*
  /// inability, and a success ends that.
  final FetchFailureClass? lastError;

  /// `last_fetch_success_at` + `last_fetch_seq`, or `null` if no fetch under
  /// this pairing has ever returned a payload.
  final FetchSuccess? lastSuccess;

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
      other.lastSuccess == lastSuccess;

  @override
  int get hashCode => Object.hash(pairingId, lastAttemptAt.toUtc(), lastError, lastSuccess);
}

/// What the store found for a pairing — **three** outcomes, not two.
///
/// The reason is not the one [BaselineState] has, and the difference is worth
/// stating so this type is not read as inheriting a security argument it does
/// not have. Damaging *this* file cannot bypass a check: without the four facts
/// no conjunct of `HOST_NOT_PUBLISHING` can be established, so the predicate
/// falls to `CANNOT_CHECK` and the phone accuses nobody.
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
