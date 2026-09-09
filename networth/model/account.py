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


__all__ = ["LinkedAccount", "ReconciliationState"]
