"""Stored-state full-sync due times for DESIGN section 13 (task 16).

A timer is only a wake-up. This reader makes the same decision after reopening
SQLite as it did before a restart, without marking work complete or touching a
provider. Dispatch, per-Item backoff and the Link worker have separate contracts.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from networth.model.figure import require_utc
from networth.staleness import UsEquityMarketCalendar

FULL_SYNC_INTERVAL = timedelta(hours=20)
FULL_SYNC_POST_CLOSE_GRACE = timedelta(hours=1)


class ScheduleStateError(RuntimeError):
    """Stored scheduling evidence is not safe to use; no row content is exposed."""


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ScheduleStateError("full-sync clock must be UTC ISO-8601 text")
    try:
        parsed = datetime.fromisoformat(value)
        require_utc(parsed, field="full-sync clock")
    except (ValueError, TypeError):
        raise ScheduleStateError("full-sync clock must be UTC ISO-8601 text") from None
    return parsed


@dataclass(frozen=True, slots=True)
class FullSyncDue:
    """The two independent reasons a full sync is due, with their evidence age."""

    checked_at: datetime
    market_ready_at: datetime
    last_successful_start: datetime | None
    last_successful_finish: datetime | None
    market_due: bool
    elapsed_due: bool

    @property
    def due(self) -> bool:
        return self.market_due or self.elapsed_due


class FullSyncSchedule:
    """Read committed full-sync successes; planning itself never consumes a due job.

    The runner records ``kind='FULL_SYNC'`` at creation and commits ``ok=1``
    with ``finished_at`` only after the full sync succeeds. An absent, failed,
    interrupted, manual or quote-only run cannot satisfy either clock. Legacy
    runs default to OTHER: one conservative catch-up is safer than guessing.

    Market readiness is satisfied by a successful run *started* at or after
    close + 1h. A run begun earlier may have fetched every account before that
    threshold even when its completion crosses it. The elapsed rule uses the
    latest successful *finish*, exactly the 20h completion clock in section 13.
    These maxima need not come from the same row if runs finish out of order.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        calendar: UsEquityMarketCalendar | None = None,
    ) -> None:
        self._connection = connection
        self._calendar = UsEquityMarketCalendar() if calendar is None else calendar

    def due(self, *, at: datetime) -> FullSyncDue:
        require_utc(at, field="scheduling time")
        if self._connection.in_transaction:
            raise ScheduleStateError("scheduling requires a connection with no active transaction")
        # One SELECT is one committed SQLite view. Parse before comparing: old
        # writers used both whole and fractional seconds, whose TEXT ordering
        # differs at the same second ('...00Z' sorts after '...00.500000Z').
        rows = self._connection.execute(
            "SELECT started_at, finished_at FROM sync_run "
            "WHERE kind = 'FULL_SYNC' AND ok = 1 AND finished_at IS NOT NULL"
        ).fetchall()
        starts: list[datetime] = []
        finishes: list[datetime] = []
        for raw_start, raw_finish in rows:
            start, finish = _timestamp(raw_start), _timestamp(raw_finish)
            if start > finish or finish > at:
                raise ScheduleStateError(
                    "full-sync success clocks are inconsistent with check time"
                )
            starts.append(start)
            finishes.append(finish)
        last_start = max(starts, default=None)
        last_finish = max(finishes, default=None)
        market_ready = (
            self._calendar.latest_completed_close(at, grace=FULL_SYNC_POST_CLOSE_GRACE)
            + FULL_SYNC_POST_CLOSE_GRACE
        )
        return FullSyncDue(
            checked_at=at,
            market_ready_at=market_ready,
            last_successful_start=last_start,
            last_successful_finish=last_finish,
            market_due=last_start is None or last_start < market_ready,
            elapsed_due=last_finish is None or at - last_finish > FULL_SYNC_INTERVAL,
        )
