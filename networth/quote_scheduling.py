"""Read the source-price clock for task 16's independent quote job."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from networth.model import FreshnessPolicy
from networth.model.figure import require_utc
from networth.staleness import UsEquityMarketCalendar
from networth.store import Store, StoredDataError


class QuoteScheduleStateError(RuntimeError):
    """Stored quote evidence is invalid; the caller must not report not-due."""


@dataclass(frozen=True, slots=True)
class QuoteRefreshDue:
    checked_at: datetime
    market_close: datetime
    target_count: int
    due_count: int

    @property
    def due(self) -> bool:
        return self.due_count > 0


class QuoteRefreshSchedule:
    """A missing/unknown price or one older than the latest close is due.

    Match ManualQuoteWorker's active, included account selection, including NEW
    accounts. Inspect observations rather than account fetch summaries or run
    success: a successful fetch can return an unchanged old price. Both full
    and quote-only cycles can supply a price. Equality at close satisfies this
    job, which has no full-sync posting grace or 20-hour fallback.

    One short read transaction covers selection and all source clocks. No
    writes, provider calls or timer stamps; restart makes the same decision.
    Malformed/future evidence raises a fixed diagnostic instead of suppressing
    work. The future dispatcher must report that refusal and leave work pending.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        calendar: UsEquityMarketCalendar | None = None,
    ) -> None:
        self._db = connection
        self._store = Store(connection)
        self._calendar = UsEquityMarketCalendar() if calendar is None else calendar

    def due(self, *, at: datetime) -> QuoteRefreshDue:
        require_utc(at, field="quote scheduling time")
        if self._db.in_transaction:
            raise ValueError("quote scheduling requires no active transaction")
        close = self._calendar.latest_completed_close(at)
        targets = due = 0
        self._db.execute("BEGIN")
        try:
            for account in self._store.accounts.for_snapshot():
                if account.freshness_policy is not FreshnessPolicy.MANUAL_QTY_LIVE_PRICE:
                    continue
                targets += 1
                observation = self._store.observations.latest_for_account(account.id)
                if observation is None:
                    due += 1
                    continue
                source = observation.figure.as_of
                if (
                    observation.observed_at > at
                    or observation.fetched_at > at
                    or (source is not None and source > at)
                ):
                    raise ValueError("future quote clock")
                if source is None or source < close:
                    due += 1
        except (TypeError, ValueError, StoredDataError):
            raise QuoteScheduleStateError("invalid stored quote scheduling evidence") from None
        finally:
            self._db.rollback()
        return QuoteRefreshDue(at, close, targets, due)
