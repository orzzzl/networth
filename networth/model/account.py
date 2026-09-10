"""Linked-account identities needed by the provider sync boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from networth.model.figure import require_nonempty
from networth.model.staleness import FreshnessPolicy

_CURRENCY = re.compile(r"[A-Z]{3}\Z")


class ReconciliationState(StrEnum):
    """Whether an account may contribute to the headline total."""

    NEW = "NEW"
    CONFIRMED = "CONFIRMED"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True, slots=True, repr=False)
class LinkedAccount:
    """One active Plaid-backed account selected for a full sync.

    The Plaid account id is deliberately absent from ``repr``. It identifies an
    account at one of the owner's institutions and must not leak through a
    batch-result or traceback merely because this value was rendered.
    """

    id: int
    item_id: int
    plaid_account_id: str
    currency: str
    freshness_policy: FreshnessPolicy
    reconciliation_state: ReconciliationState

    def __post_init__(self) -> None:
        for field, value in (("id", self.id), ("item_id", self.item_id)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{field} must be an integer")
            if value <= 0:
                raise ValueError(f"{field} must be positive")
        require_nonempty(self.plaid_account_id, field="plaid_account_id")
        if not isinstance(self.currency, str) or _CURRENCY.fullmatch(self.currency) is None:
            raise ValueError("currency must be a three-letter uppercase code")
        if not isinstance(self.freshness_policy, FreshnessPolicy):
            raise TypeError("freshness_policy must be a FreshnessPolicy")
        if not self.freshness_policy.requires_item:
            raise ValueError("a linked account requires a synced freshness policy")
        if not isinstance(self.reconciliation_state, ReconciliationState):
            raise TypeError("reconciliation_state must be a ReconciliationState")

    def __repr__(self) -> str:
        return (
            f"LinkedAccount(id={self.id}, item_id={self.item_id}, "
            "plaid_account_id=<redacted>, "
            f"currency={self.currency!r}, freshness_policy={self.freshness_policy!r}, "
            f"reconciliation_state={self.reconciliation_state!r})"
        )


@dataclass(frozen=True, slots=True)
class SnapshotAccount:
    """One active, included account considered by the snapshotter.

    ``account_count`` includes every record of this type, including ``NEW``
    accounts that deliberately contribute no value. Accounts excluded by the
    owner and either form of archived/superseded account never become this type.
    """

    id: int
    item_id: int | None
    currency: str
    sign: int
    freshness_policy: FreshnessPolicy
    reconciliation_state: ReconciliationState

    def __post_init__(self) -> None:
        if not isinstance(self.id, int) or isinstance(self.id, bool):
            raise TypeError("id must be an integer")
        if self.id <= 0:
            raise ValueError("id must be positive")
        if self.item_id is not None:
            if not isinstance(self.item_id, int) or isinstance(self.item_id, bool):
                raise TypeError("item_id must be an integer or None")
            if self.item_id <= 0:
                raise ValueError("item_id must be positive")
        if not isinstance(self.currency, str) or _CURRENCY.fullmatch(self.currency) is None:
            raise ValueError("currency must be a three-letter uppercase code")
        if not isinstance(self.sign, int) or isinstance(self.sign, bool):
            raise TypeError("sign must be an integer")
        if self.sign not in (-1, 1):
            raise ValueError("sign must be -1 or 1")
        if not isinstance(self.freshness_policy, FreshnessPolicy):
            raise TypeError("freshness_policy must be a FreshnessPolicy")
        if self.freshness_policy.requires_item != (self.item_id is not None):
            owner = "requires" if self.freshness_policy.requires_item else "cannot have"
            raise ValueError(f"{self.freshness_policy.value} {owner} an Item")
        if not isinstance(self.reconciliation_state, ReconciliationState):
            raise TypeError("reconciliation_state must be a ReconciliationState")
        if self.reconciliation_state is ReconciliationState.ARCHIVED:
            raise ValueError("an archived account cannot enter a snapshot")


__all__ = ["LinkedAccount", "ReconciliationState", "SnapshotAccount"]
