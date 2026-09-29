"""Task 16 full-sync and health dispatch; not yet the complete scheduled cycle or CLI.

The caller supplies the canonical sync lock path for the selected database.
All dispatchers for that database must use that same path. The connection and
workers are owned here so checking its transaction state checks the actual
network caller's connection, not an unrelated handle supplied alongside it.
"""

from __future__ import annotations

import logging
import random
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from networth.alerts import AlertEvaluation
from networth.cycle_alerts import CycleAlertEvaluator
from networth.filelock import exclusive_file_lock
from networth.item_health import ItemHealthPoller, PollBatchResult, _ItemGetter
from networth.model.figure import require_utc
from networth.scheduling import FullSyncSchedule, ScheduleStateError
from networth.store import Store
from networth.sync import BalanceMode, FullSync, FullSyncPlan, _SyncClient, _TokenResolver

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _now() -> datetime:
    return datetime.now(UTC)


def _text(at: datetime) -> str:
    require_utc(at, field="dispatch time")
    return at.isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Safe run summary; no account values, provider ids or exception text."""

    state: str
    run_id: str | None = None
    ok: bool | None = None
    schedule_state_error: bool = False


class _Dispatcher:
    """Shared connection ownership, admission and short write retry policy."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        lock_path: Path,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None],
    ) -> None:
        self._db = connection
        self._idle()
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._store = Store(connection)
        self._lock_path = lock_path
        self._clock = clock
        self._sleep = sleep
        self._active = False

    def _idle(self) -> None:
        if self._db.in_transaction:
            raise ValueError("dispatch requires a connection with no active transaction")

    def _write(self, operation: Callable[[], T]) -> T:
        self._idle()
        for attempt in range(3):
            try:
                self._db.execute("BEGIN IMMEDIATE")
                result = operation()
                self._db.commit()
                return result
            except BaseException as exc:
                if self._db.in_transaction:
                    self._db.rollback()
                code = getattr(exc, "sqlite_errorcode", None)
                busy = isinstance(exc, sqlite3.OperationalError) and (
                    code is not None and code & 0xFF == sqlite3.SQLITE_BUSY
                )
                if not busy or attempt == 2:
                    raise
                self._sleep(random.uniform(0.05, 0.15) * (2**attempt))
        raise AssertionError("unreachable write retry")


class FullSyncDispatcher(_Dispatcher):
    """Admit one run, collect without a transaction, then commit its outcome.

    Run creation is durable before provider calls. A crash or unexpected error
    leaves that run unfinished and due; no recovery path guesses a success.
    Only SQLITE_BUSY is retried, at most three write attempts, with rollback
    before replay. Collection is never inside that retry. Provider failures
    use persisted per-Item 1h/2h/4h/8h delays, reset by a successful Item fetch.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        client: _SyncClient,
        tokens: _TokenResolver,
        *,
        balance_mode: BalanceMode,
        lock_path: Path,
        clock: Callable[[], datetime] = _now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(connection, lock_path=lock_path, clock=clock, sleep=sleep)
        self._worker = FullSync(self._store, client, tokens, balance_mode=balance_mode)
        self._schedule = FullSyncSchedule(connection)

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
        require_utc(started, field="dispatch time")
        state_error = False
        try:
            due = self._schedule.due(at=started).due
        except ScheduleStateError:
            logger.error("full-sync stored clocks invalid; treating full sync as due")
            due = True
            state_error = True
        if not due:
            return DispatchResult("NOT_DUE")
        deferred = self._deferred(started)
        targets = {account.item_id for account in self._store.accounts.syncable()}
        if targets and targets <= deferred:
            return DispatchResult("DEFERRED", schedule_state_error=state_error)

        run_id = uuid4().hex

        def begin() -> None:
            self._db.execute(
                'INSERT INTO sync_run(id, started_at, "trigger", kind) '
                "VALUES (?, ?, 'SCHEDULED', 'FULL_SYNC')",
                (run_id, _text(started)),
            )

        self._write(begin)
        # PR122 review: this must be enforced at the actual caller. A future
        # refactor that leaves run creation uncommitted must fail before I/O.
        self._idle()
        plan = self._worker.collect(run_id, at=started, deferred_item_ids=deferred)
        ok = self._complete(run_id, plan, started)
        return DispatchResult("COMPLETED", run_id, ok, state_error)

    def _complete(self, run_id: str, plan: FullSyncPlan, started: datetime) -> bool:
        finished = self._clock()
        require_utc(finished, field="dispatch completion time")
        if finished < started:
            raise ValueError("dispatch completion precedes its start")
        return self._write(lambda: self._finish(run_id, plan, finished))

    def _finish(self, run_id: str, plan: FullSyncPlan, finished: datetime) -> bool:
        """Persist the Plaid outcome inside the caller's write transaction."""
        result = self._worker.persist(plan)
        self._record_retries(plan, finished)
        self._db.execute(
            "UPDATE sync_run SET finished_at = ?, ok = ? WHERE id = ?",
            (_text(finished), int(result.ok), run_id),
        )
        return result.ok

    def _deferred(self, at: datetime) -> frozenset[int]:
        deferred: set[int] = set()
        for item_id, raw_time in self._db.execute(
            "SELECT item_id, next_attempt_at FROM full_sync_retry"
        ):
            try:
                retry_at = datetime.fromisoformat(raw_time)
                require_utc(retry_at, field="retry time")
            except (TypeError, ValueError):
                raise ValueError("invalid stored full-sync retry time") from None
            if retry_at > at:
                deferred.add(item_id)
        return frozenset(deferred)

    def _record_retries(self, plan: FullSyncPlan, at: datetime) -> None:
        for outcome in plan.items:
            if not outcome.attempted:
                continue
            if not outcome.failed:
                self._db.execute(
                    "DELETE FROM full_sync_retry WHERE item_id = ?", (outcome.item_id,)
                )
                continue
            row = self._db.execute(
                "SELECT failures FROM full_sync_retry WHERE item_id = ?", (outcome.item_id,)
            ).fetchone()
            failures = 1 if row is None else min(row[0] + 1, 4)
            retry_at = at + timedelta(hours=2 ** (failures - 1))
            self._db.execute(
                "INSERT INTO full_sync_retry(item_id, failures, next_attempt_at) VALUES (?, ?, ?) "
                "ON CONFLICT(item_id) DO UPDATE SET failures = excluded.failures, "
                "next_attempt_at = excluded.next_attempt_at",
                (outcome.item_id, failures, _text(retry_at)),
            )


class HealthDispatcher(_Dispatcher):
    """Poll hourly from committed Item clocks under the canonical sync lock.

    No sync_run row is needed: only persisted health observations consume due
    work, and these must never advance the full-sync clock. Failed collection
    targets retain their old clock and are attempted again next activation.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        client: _ItemGetter,
        tokens: _TokenResolver,
        *,
        lock_path: Path,
        clock: Callable[[], datetime] = _now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(connection, lock_path=lock_path, clock=clock, sleep=sleep)
        self._worker = ItemHealthPoller(self._store.items, client, tokens)

    def run_due(self) -> PollBatchResult:
        self._idle()
        if self._active:
            raise ValueError("dispatch is already active")
        with exclusive_file_lock(self._lock_path, blocking=False, reentrant=False):
            self._active = True
            try:
                at = self._clock()
                self._idle()
                plan = self._worker.collect_due(at=at)
                if not plan.attempted_count:
                    return PollBatchResult(0, (), ())
                return self._write(lambda: self._worker.persist(plan))
            finally:
                self._active = False


class AlertDispatcher(_Dispatcher):
    """Reassess stored facts on each activation, even without a new snapshot.

    Advancing wall time can make an unchanged source frozen or a share count
    overdue. No successful sync or timer stamp is required to evaluate it.
    The future cycle runner must call this after collection/persistence and
    before Publisher.publish(); this class does not publish by itself.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        lock_path: Path,
        clock: Callable[[], datetime] = _now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(connection, lock_path=lock_path, clock=clock, sleep=sleep)
        self._evaluator = CycleAlertEvaluator(connection)

    def run(self) -> AlertEvaluation:
        self._idle()
        if self._active:
            raise ValueError("dispatch is already active")
        with exclusive_file_lock(self._lock_path, blocking=False, reentrant=False):
            self._active = True
            try:
                at = self._clock()
                require_utc(at, field="alert evaluation time")
                return self._write(lambda: self._evaluator.evaluate(at=at))
            finally:
                self._active = False
