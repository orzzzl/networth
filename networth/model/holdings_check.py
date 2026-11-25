"""Whether an institution's own total confirms an account's holdings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from networth.model.figure import require_minor_units, require_nonempty, require_utc


class HoldingsCheckOutcome(StrEnum):
    """How a total was found to describe a session, or that it was not."""

    CONFIRMED = "CONFIRMED"
    UNCHANGED = "UNCHANGED"
    UNCONFIRMED = "UNCONFIRMED"


@dataclass(frozen=True, slots=True, repr=False)
class HoldingsCheck:
    """One sync run's check of one account's total against its holdings.

    ``close_session`` is the one market session the reply's close prices
    describe, and ``at_close_minor`` the holdings valued at them; both are
    absent when the prices describe no single session. ``session`` is the one
    the total was established to describe — by those prices when
    ``CONFIRMED``, carried from an earlier check when ``UNCHANGED`` — and is
    absent exactly when ``UNCONFIRMED``. Figures stay out of ``repr``.
    """

    sync_run_id: str
    account_id: int
    checked_at: datetime
    total_minor: int
    close_session: date | None
    at_close_minor: int | None
    outcome: HoldingsCheckOutcome
    session: date | None

    def __post_init__(self) -> None:
        require_nonempty(self.sync_run_id, field="sync_run_id")
        if not isinstance(self.account_id, int) or isinstance(self.account_id, bool):
            raise TypeError("account_id must be an integer")
        if self.account_id <= 0:
            raise ValueError("account_id must be positive")
        require_utc(self.checked_at, field="checked_at")
        require_minor_units(self.total_minor, field="total_minor")
        for field, value in (("close_session", self.close_session), ("session", self.session)):
            if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
                raise TypeError(f"{field} must be a date")
        if self.at_close_minor is not None:
            require_minor_units(self.at_close_minor, field="at_close_minor")
        if not isinstance(self.outcome, HoldingsCheckOutcome):
            raise TypeError("outcome must be a HoldingsCheckOutcome")
        if (self.close_session is None) != (self.at_close_minor is None):
            raise ValueError("close prices value the holdings exactly when they name a session")
        if (self.outcome is HoldingsCheckOutcome.UNCONFIRMED) != (self.session is None):
            raise ValueError("a check names its session exactly when it is not UNCONFIRMED")
        if self.outcome is HoldingsCheckOutcome.CONFIRMED and self.session != self.close_session:
            raise ValueError("a CONFIRMED check describes the session its close prices name")

    def __repr__(self) -> str:
        return (
            f"HoldingsCheck(account_id={self.account_id}, outcome={self.outcome.value}, "
            f"session={self.session!r}, close_session={self.close_session!r}, "
            "total_minor=<redacted>, at_close_minor=<redacted>)"
        )


__all__ = ["HoldingsCheck", "HoldingsCheckOutcome"]
