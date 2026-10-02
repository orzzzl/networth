"""Task 16 quote-only cycles, using stored prices rather than timer stamps."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from networth.cycle_alerts import CycleAlertEvaluator
from networth.dispatch import DispatchResult, _Dispatcher, _now, _text
from networth.filelock import exclusive_file_lock
from networth.manual_quotes import ManualQuoteWorker, _Quotes
from networth.model import FreshnessPolicy, ReconciliationState
from networth.model.figure import require_utc
from networth.quote_scheduling import QuoteRefreshSchedule
from networth.snapshotter import SnapshotInputError, Snapshotter


class QuoteCycleDispatcher(_Dispatcher):
    """Refresh manual prices and atomically build an honestly aged snapshot.

    This job has no Plaid client. Existing linked observations are carried into
    the new run with unchanged source/fetch clocks and an explicit carry flag.
    Missing linked values refuse the snapshot; NEW accounts remain excluded by
    Snapshotter. Carried accounts' fetch summaries and Item retry state do not
    change. Runs use OTHER, so quote success never satisfies full-sync due work.

    Bad scheduling evidence raises QuoteScheduleStateError before collection.
    An error after admission leaves a durable unfinished run. BUSY retries
    replay only the write phase, while restart recollects idempotent quotes.
    A successful fetch of an old price stays due on the next activation.
    Publication is independent and must follow this transaction's commit.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        quotes: _Quotes,
        *,
        lock_path: Path,
        clock: Callable[[], datetime] = _now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(connection, lock_path=lock_path, clock=clock, sleep=sleep)
        self._schedule = QuoteRefreshSchedule(connection)
        self._manual = ManualQuoteWorker(connection, quotes, clock=clock)
        self._snapshotter = Snapshotter(self._store)
        self._alerts = CycleAlertEvaluator(connection)

    def run_due(self) -> DispatchResult:
        self._idle()
        if self._active:
            raise ValueError("dispatch is already active")
        with exclusive_file_lock(self._lock_path, blocking=False, reentrant=False):
            self._active = True
            try:
                return self._run_locked()
            finally:
                self._active = False

    def _run_locked(self) -> DispatchResult:
        started = self._clock()
        require_utc(started, field="quote dispatch time")
        if not self._schedule.due(at=started).due:
            # The runtime evaluates alerts independently even when no quote is
            # due. Idle quote admission itself needs no SQLite writer lock.
            return DispatchResult("NOT_DUE")

        run_id = uuid4().hex

        def begin() -> None:
            self._db.execute(
                'INSERT INTO sync_run(id, started_at, "trigger", kind) '
                "VALUES (?, ?, 'QUOTE_REFRESH', 'OTHER')",
                (run_id, _text(started)),
            )

        self._write(begin)
        self._idle()
        plan = self._manual.collect(run_id, at=started)
        finished = self._clock()
        require_utc(finished, field="quote completion time")
        if finished < started:
            raise ValueError("quote completion precedes its start")

        def finish() -> None:
            self._manual.persist(plan, at=finished)
            self._carry_linked(run_id, at=finished)
            self._db.execute(
                "UPDATE sync_run SET finished_at = ?, ok = 1 WHERE id = ?",
                (_text(finished), run_id),
            )
            self._snapshotter.run(run_id, at=finished)
            self._alerts.evaluate(at=finished)

        self._write(finish)
        return DispatchResult("COMPLETED", run_id, True)

    def _carry_linked(self, run_id: str, *, at: datetime) -> None:
        """Read and carry current values under the final write transaction.

        Do not retain an earlier read across quote I/O: an independent writer
        may have updated account selection or supplied a newer linked value.
        Manual-static revisions are selected directly by Snapshotter.
        """
        for account in self._store.accounts.for_snapshot():
            if (
                account.freshness_policy
                in (
                    FreshnessPolicy.MANUAL_QTY_LIVE_PRICE,
                    FreshnessPolicy.MANUAL_STATIC,
                )
                or account.reconciliation_state is ReconciliationState.NEW
            ):
                continue
            previous = self._store.observations.latest_for_account(account.id)
            if previous is None:
                raise SnapshotInputError("quote cycle has no linked value to carry")
            if (
                previous.observed_at > at
                or previous.fetched_at > at
                or (previous.figure.as_of is not None and previous.figure.as_of > at)
            ):
                raise SnapshotInputError("quote cycle cannot carry future linked evidence")
            self._store.observations.append(
                replace(
                    previous.as_draft(),
                    sync_run_id=run_id,
                    observed_at=at,
                    is_carried_forward=True,
                )
            )
