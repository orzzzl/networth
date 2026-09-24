import 'package:flutter/foundation.dart';

import 'publication_seq.dart';

/// Why I6 refused a payload. The two refusals in `DESIGN.md` §9.3's table share
/// a warning but not a cause, and the owner's next move differs.
enum DowngradeCause {
  /// `seq < last_seq` — something served an envelope older than the one held.
  /// §9.3's realistic triggers are all operator error: a database restored onto
  /// a running daemon, a rollback to an older build, two daemons answering to
  /// the same name.
  olderPublication,

  /// The payload named a `pairing_id` that is not the paired one — "the phone
  /// is talking to something that is not its daemon".
  foreignPairing,
}

/// The persistent warning §9.3 raises, and why it is on disk.
///
/// *"It survives restarts and clears only when a `seq` greater than `last_seq`
/// actually arrives — not on the next successful fetch, which would let a single
/// good response paper over an unexplained downgrade."* A warning held in memory
/// would be cleared by the one event that proves nothing: the app being reopened.
@immutable
class DowngradeWarning {
  const DowngradeWarning({required this.cause, required this.at, this.refusedSeq});

  final DowngradeCause cause;

  /// When the refusal happened, by the device's own clock.
  ///
  /// Device time, and deliberately: this is a phone-local event with no host
  /// timestamp to borrow. §9.1's clock-skew rules are about deciding a copy's
  /// *age* against the host's clock; nothing here is compared with the host, so
  /// a skewed device makes this display wrong rather than the warning wrong.
  final DateTime at;

  /// The `seq` that was refused — null for [DowngradeCause.foreignPairing].
  ///
  /// A foreign pairing's payload does carry a `seq`, and it is deliberately not
  /// kept: it belongs to a counter this phone has no baseline for, so storing it
  /// beside [SeqBaseline.lastSeq] would put two unrelated numbers in comparable
  /// positions. There is nothing true to say about their order.
  final PublicationSeq? refusedSeq;

  @override
  bool operator ==(Object other) =>
      other is DowngradeWarning &&
      other.cause == cause &&
      other.at.isAtSameMomentAs(at) &&
      other.refusedSeq == refusedSeq;

  @override
  int get hashCode => Object.hash(cause, at.toUtc(), refusedSeq);
}

/// The baseline I6 compares against: one pairing, the `seq` held under it, and
/// any unexplained downgrade seen under it.
///
/// **The `pairing_id` is part of the record rather than a key into a table of
/// them, and that is the whole scoping rule.** §9.3 point 3 stores `last_seq`
/// per `pairing_id` so that a phone with no baseline for its current pairing
/// accepts its first payload on trust — which is what makes §9.3a's restore
/// recoverable by re-pairing instead of by teaching the owner to dismiss an
/// integrity alarm. Only ever one pairing is current ([PairingVault] holds it),
/// and a `pairing_id` is minted fresh by the daemon at pairing time, so a record
/// for any *other* pairing could never be consulted again. Keeping a map of them
/// would be unbounded growth with an eviction policy to get wrong, in exchange
/// for answering a question nobody asks.
@immutable
class SeqBaseline {
  const SeqBaseline({required this.pairingId, required this.lastSeq, this.warning});

  final String pairingId;

  /// The `seq` of the payload the phone actually holds under [pairingId].
  final PublicationSeq lastSeq;

  /// The unexplained downgrade seen under [pairingId], if one has been.
  ///
  /// There is deliberately no `heldSeq` beside it. At the moment of a refusal it
  /// would equal [lastSeq], and the only event that advances [lastSeq] is the
  /// arrival of a greater `seq` — which clears the warning. The two can never
  /// legitimately differ, so a second copy of the number would be state that can
  /// only ever be made *in*consistent.
  final DowngradeWarning? warning;

  @override
  bool operator ==(Object other) =>
      other is SeqBaseline &&
      other.pairingId == pairingId &&
      other.lastSeq == lastSeq &&
      other.warning == warning;

  @override
  int get hashCode => Object.hash(pairingId, lastSeq, warning);
}

/// What the store found for a pairing — **three** outcomes, not two.
///
/// This type exists because of the one way I6 can be disabled without anybody
/// noticing. §9.3 point 3 says a phone with no `last_seq` for its current
/// pairing accepts its first payload on trust; a baseline file that *exists and
/// does not parse* is not that phone, but it looks exactly like it to any reader
/// that recovers from a damaged file by treating it as empty. Corruption would
/// then be the bypass: damage the file, and the next payload served is accepted
/// and becomes the new baseline.
///
/// So "nothing is stored" and "something is stored and cannot be read" are
/// different values of a `sealed` type rather than the same `null`, and Dart's
/// exhaustive `switch` makes the caller write the branch. A thrown exception
/// would also be unmissable, right up until a caller caught it and defaulted —
/// which is the shape of the mistake, not a different mistake.
sealed class BaselineState {
  const BaselineState();
}

/// Nothing is stored for this pairing: a new install, or a pairing this phone
/// has not yet accepted a payload under. §9.3a's accept-on-trust case.
final class BaselineAbsent extends BaselineState {
  const BaselineAbsent();
}

/// A baseline for this pairing, read back intact.
final class BaselineHeld extends BaselineState {
  const BaselineHeld(this.baseline);

  final SeqBaseline baseline;
}

/// A baseline exists and could not be read. **Never** accept-on-trust.
final class BaselineUnreadable extends BaselineState {
  const BaselineUnreadable(this.reason);

  /// What was wrong, for the screen. Carries no stored bytes.
  final String reason;
}
