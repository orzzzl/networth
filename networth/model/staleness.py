"""The durable vocabulary for the two independent staleness axes.

Axis A is :class:`~networth.model.item.ItemState`: whether an institution can
still be reached.  Axis B below answers a different question: how old the
account's own source data is.  Keeping separate enums makes it impossible to
collapse "the call worked" into "the number is fresh" at the type boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from networth.model.figure import require_nonnegative_int, require_utc
from networth.model.item import ItemState

# The single executable definition of DESIGN section 11's escalation rule.
# Both the evaluator and task 15's alerting consume this policy from here.
FROZEN_MARKET_DAYS = 5


class FreshnessPolicy(StrEnum):
    """The four policies stored on ``account.freshness_policy``."""

    SYNCED_HOLDINGS = "SYNCED_HOLDINGS"
    SYNCED_BALANCE = "SYNCED_BALANCE"
    MANUAL_STATIC = "MANUAL_STATIC"
    MANUAL_QTY_LIVE_PRICE = "MANUAL_QTY_LIVE_PRICE"

    @property
    def requires_item(self) -> bool:
        """Whether the account has an Item and therefore an Axis A state."""

        return self in (FreshnessPolicy.SYNCED_HOLDINGS, FreshnessPolicy.SYNCED_BALANCE)


class FreshnessState(StrEnum):
    """Axis B, including the policy-static and escalation states."""

    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    FROZEN = "FROZEN"
    STATIC = "STATIC"


class DisplayState(StrEnum):
    """The daemon-evaluated connection display states from DESIGN section 9.2."""

    OK = "OK"
    WAITING = "WAITING"
    ACTION_NEEDED = "ACTION_NEEDED"


@dataclass(frozen=True, slots=True)
class FreshnessAssessment:
    """One account's Axis B result, alongside (but not merged with) Axis A.

    ``fetched_at`` is intentionally absent.  The machine receives it through an
    observation so a test can prove that a successful new call does not refresh
    this result, but only ``source_as_of`` is evidence carried out of the
    evaluation.
    """

    state: FreshnessState
    source_as_of: datetime | None
    market_days_without_advance: int
    is_carried_forward: bool
    item_state: ItemState | None

    def __post_init__(self) -> None:
        if not isinstance(self.state, FreshnessState):
            raise TypeError("state must be a FreshnessState")
        if self.source_as_of is not None:
            require_utc(self.source_as_of, field="source_as_of")
        if (self.state is FreshnessState.UNKNOWN) != (self.source_as_of is None):
            raise ValueError("UNKNOWN freshness has no source_as_of; every other state has one")
        require_nonnegative_int(
            self.market_days_without_advance,
            field="market_days_without_advance",
        )
        if not isinstance(self.is_carried_forward, bool):
            raise TypeError("is_carried_forward must be a bool")
        if self.item_state is not None and not isinstance(self.item_state, ItemState):
            raise TypeError("item_state must be an ItemState or None")
        if (
            self.state in (FreshnessState.UNKNOWN, FreshnessState.STATIC)
            and self.market_days_without_advance != 0
        ):
            raise ValueError(f"{self.state.value} freshness cannot accumulate market days")
        qualifies_as_frozen = (
            self.item_state is ItemState.HEALTHY
            and self.market_days_without_advance >= FROZEN_MARKET_DAYS
        )
        if (self.state is FreshnessState.FROZEN) != qualifies_as_frozen:
            raise ValueError(
                "FROZEN requires a HEALTHY Item and the shared market-day threshold; "
                "that condition must also be labelled FROZEN"
            )

    @property
    def is_fresh(self) -> bool:
        """Only advancing data inside its window is called fresh.

        ``STATIC`` is healthy by policy but deliberately not relabelled fresh,
        and ``UNKNOWN`` can therefore never render as fresh.
        """

        return self.state is FreshnessState.FRESH

    @property
    def frozen_alert_required(self) -> bool:
        """Task 15 consumes this same Axis B state instead of copying a threshold."""

        return self.state is FreshnessState.FROZEN


__all__ = [
    "DisplayState",
    "FROZEN_MARKET_DAYS",
    "FreshnessAssessment",
    "FreshnessPolicy",
    "FreshnessState",
]
