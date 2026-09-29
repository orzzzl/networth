"""Task 16 full-cycle completion, before independent publication and archives.

Keep the Plaid-only dispatcher as a component seam. Scheduled full cycles must
use this runner so a manual valuation or snapshot failure cannot consume their
stored due time. No CLI or installed service selects either dispatcher yet.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from networth.cycle_alerts import CycleAlertEvaluator
from networth.dispatch import DispatchResult, FullSyncDispatcher, _now
from networth.manual_quotes import ManualQuoteWorker, _Quotes
from networth.model.figure import require_utc
from networth.snapshotter import Snapshotter
from networth.sync import BalanceMode, FullSyncPlan, _SyncClient, _TokenResolver


class FullCycleDispatcher(FullSyncDispatcher):
    """Commit full-run observations, success, snapshot and alerts atomically.

    Admission, due clocks and Item retry policy are inherited. Quote collection
    holds the sync file lock but no SQLite transaction. Only a successful Plaid
    plan needs manual prices: a failed plan records its retries and alerts, with
    no snapshot. All-deferred and not-due activations still reassess alerts.

    Missing quotes, concurrent manual edits, snapshot refusal or alert failure
    leave the run unfinished. A later activation recollects idempotent data;
    only BUSY persistence retries reuse the in-memory plans. Publication must
    follow this commit and retry independently, never call inside _complete.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        client: _SyncClient,
        tokens: _TokenResolver,
        quotes: _Quotes,
        *,
        balance_mode: BalanceMode,
        lock_path: Path,
        clock: Callable[[], datetime] = _now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(
            connection,
            client,
            tokens,
            balance_mode=balance_mode,
            lock_path=lock_path,
            clock=clock,
            sleep=sleep,
        )
        self._manual = ManualQuoteWorker(connection, quotes, clock=clock)
        self._snapshotter = Snapshotter(self._store)
        self._alerts = CycleAlertEvaluator(connection)

    def _run_locked(self) -> DispatchResult:
        result = super()._run_locked()
        if result.state != "COMPLETED":
            at = self._clock()
            require_utc(at, field="alert evaluation time")
            self._write(lambda: self._alerts.evaluate(at=at))
        return result

    def _complete(self, run_id: str, plan: FullSyncPlan, started: datetime) -> bool:
        self._idle()
        manual = None if plan.failures else self._manual.collect(run_id, at=started)
        finished = self._clock()
        require_utc(finished, field="cycle completion time")
        if finished < started:
            raise ValueError("cycle completion precedes its start")

        def finish() -> bool:
            ok = self._finish(run_id, plan, finished)
            if manual is not None:
                self._manual.persist(manual, at=finished)
            if ok:
                # SnapshotRepository requires ok=1. That provisional row and
                # every cycle output stay invisible until this transaction commits.
                self._snapshotter.run(run_id, at=finished)
            self._alerts.evaluate(at=finished)
            return ok

        return self._write(finish)
