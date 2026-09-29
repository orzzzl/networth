"""Task 16 full-sync dispatch; not yet the complete scheduled cycle or CLI.

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

from networth.filelock import exclusive_file_lock
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


class FullSyncDispatcher:
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
        self._db = connection
        self._idle()
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._store = Store(connection)
        self._worker = FullSync(self._store, client, tokens, balance_mode=balance_mode)
        self._schedule = FullSyncSchedule(connection)
        self._lock_path = lock_path
        self._clock = clock
        self._sleep = sleep
        self._active = False

    def _idle(self) -> None:
        if self._db.in_transaction:
            raise ValueError("dispatch requires a connection with no active transaction")

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
        finished = self._clock()
        require_utc(finished, field="dispatch completion time")
        if finished < started:
            raise ValueError("dispatch completion precedes its start")

        def finish() -> bool:
            result = self._worker.persist(plan)
            self._record_retries(plan, finished)
            self._db.execute(
                "UPDATE sync_run SET finished_at = ?, ok = ? WHERE id = ?",
                (_text(finished), int(result.ok), run_id),
            )
            return result.ok

        ok = self._write(finish)
        return DispatchResult("COMPLETED", run_id, ok, state_error)

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
