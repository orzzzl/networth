"""Pairing-scoped downgrade checks exercised by every restore drill.

Task 19 will own the production payload serializer.  Task 03a owns the restore
invariant that serializer must preserve, so this module keeps the smallest
executable model of I6: authenticated envelopes, a baseline scoped to one
pairing, and a persistent downgrade warning.  The drill runs all five §9.3a
cases independently against this model.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import StrEnum

from networth.backup.crypto import AuthenticationError, open_sealed, seal


class ReplayVerdict(StrEnum):
    ACCEPTED = "accepted"
    UNCHANGED = "unchanged"
    REFUSED = "refused"


def envelope(pairing_id: str, seq: int, key: bytes) -> bytes:
    if not pairing_id or seq <= 0:
        raise ValueError("an envelope requires a pairing_id and positive seq")
    payload = json.dumps(
        {"pairing_id": pairing_id, "seq": seq}, sort_keys=True, separators=(",", ":")
    ).encode()
    return seal(payload, key)


@dataclass(slots=True)
class PairingScopedGuard:
    pairing_id: str
    key: bytes
    last_seq: int | None = None
    downgrade_warning: bool = False

    def receive(self, candidate: bytes) -> ReplayVerdict:
        try:
            plaintext = open_sealed(candidate, self.key)
            raw = json.loads(plaintext)
        except (AuthenticationError, UnicodeDecodeError, json.JSONDecodeError):
            self.downgrade_warning = True
            return ReplayVerdict.REFUSED
        if not isinstance(raw, dict):
            self.downgrade_warning = True
            return ReplayVerdict.REFUSED
        pairing_id = raw.get("pairing_id")
        seq = raw.get("seq")
        if (
            pairing_id != self.pairing_id
            or not isinstance(seq, int)
            or isinstance(seq, bool)
            or seq <= 0
        ):
            self.downgrade_warning = True
            return ReplayVerdict.REFUSED
        if self.last_seq is not None and seq < self.last_seq:
            self.downgrade_warning = True
            return ReplayVerdict.REFUSED
        if self.last_seq == seq:
            return ReplayVerdict.UNCHANGED
        self.last_seq = seq
        self.downgrade_warning = False
        return ReplayVerdict.ACCEPTED


@dataclass(frozen=True, slots=True)
class ReplayDrillResult:
    restore_repaired_by_repairing: bool
    lower_seq_refused: bool
    old_pairing_refused: bool
    rollback_without_repair_refused: bool
    same_archive_twice_accepted: bool

    @property
    def passed(self) -> bool:
        return all(
            (
                self.restore_repaired_by_repairing,
                self.lower_seq_refused,
                self.old_pairing_refused,
                self.rollback_without_repair_refused,
                self.same_archive_twice_accepted,
            )
        )


def run_replay_drill(restored_seq: int) -> ReplayDrillResult:
    """Run §9.3a's five cases without consulting a clock or another host."""

    if restored_seq < 0:
        raise ValueError("restored seq cannot be negative")
    first_seq = restored_seq + 1
    old_pairing, new_pairing = "pairing-before-restore", "pairing-after-restore"
    old_key, new_key = os.urandom(32), os.urandom(32)

    # (1) A fresh pairing owns a fresh baseline even though the counter rewound.
    repaired = PairingScopedGuard(new_pairing, new_key)
    case_one = repaired.receive(envelope(new_pairing, first_seq, new_key))

    # (2) Within that pairing, moving forward then replaying lower is refused.
    repaired.receive(envelope(new_pairing, first_seq + 1, new_key))
    case_two = repaired.receive(envelope(new_pairing, first_seq, new_key))

    # (3) The old key/pairing cannot authenticate to the new pairing at all.
    case_three = repaired.receive(envelope(old_pairing, first_seq + 500, old_key))

    # (4) A rollback without a re-pair remains below the same baseline.
    rollback_guard = PairingScopedGuard(new_pairing, new_key, last_seq=first_seq + 20)
    case_four = rollback_guard.receive(envelope(new_pairing, first_seq, new_key))

    # (5) Two restores of the same counter are independent after separate pairs.
    key_three, key_four = os.urandom(32), os.urandom(32)
    third = PairingScopedGuard("pairing-third", key_three)
    fourth = PairingScopedGuard("pairing-fourth", key_four)
    case_five = (
        third.receive(envelope(third.pairing_id, first_seq, key_three)),
        fourth.receive(envelope(fourth.pairing_id, first_seq, key_four)),
    )

    return ReplayDrillResult(
        restore_repaired_by_repairing=case_one is ReplayVerdict.ACCEPTED,
        lower_seq_refused=case_two is ReplayVerdict.REFUSED and repaired.downgrade_warning,
        old_pairing_refused=case_three is ReplayVerdict.REFUSED,
        rollback_without_repair_refused=case_four is ReplayVerdict.REFUSED,
        same_archive_twice_accepted=case_five == (ReplayVerdict.ACCEPTED, ReplayVerdict.ACCEPTED),
    )
