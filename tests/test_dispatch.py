"""The runtime seam is exercised with migrated WAL files and real workers."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar

import pytest

from networth.dispatch import FullSyncDispatcher
from networth.filelock import LockUnavailable, exclusive_file_lock
from networth.plaid import BalanceRecord, InvestmentRecords
from networth.scheduling import FullSyncSchedule
from networth.storage import migrate
from networth.sync import BalanceMode, FullSyncPlan
from tests.test_sync import FakeTokens, add_account, add_item, balance

NOW = datetime(2026, 9, 18, 21, tzinfo=UTC)
T = TypeVar("T")


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(tmp_path / "dispatch.db")
    migrate(connection)
    connection.execute(
        "INSERT INTO institution(plaid_institution_id, name, is_oauth) "
        "VALUES ('synthetic-institution', 'Synthetic institution', 0)"
    )
    for suffix in ("one", "two"):
        add_account(connection, add_item(connection, suffix), suffix)
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


class Client:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: set[str] = set()
        self.before: Callable[[], None] = lambda: None

    def fetch_realtime_balances(self, access_token: str) -> tuple[BalanceRecord, ...]:
        self.calls.append(access_token)
        self.before()
        if access_token in self.fail:
            raise RuntimeError("synthetic-private-provider-detail")
        return (balance(access_token.removeprefix("material-")),)

    def fetch_cached_balances(self, access_token: str) -> tuple[BalanceRecord, ...]:
        return self.fetch_realtime_balances(access_token)

    def fetch_holdings(self, access_token: str) -> InvestmentRecords:
        raise AssertionError("no holdings targets")


def dispatcher(
    db: sqlite3.Connection, tmp_path: Path, client: Client, *, at: datetime = NOW
) -> FullSyncDispatcher:
    return FullSyncDispatcher(
        db,
        client,
        FakeTokens(),
        balance_mode=BalanceMode.REALTIME,
        lock_path=tmp_path / "sync.lock",
        clock=lambda: at,
    )


def test_provider_calls_leave_writer_free_and_success_is_durable(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    client = Client()
    other = sqlite3.connect(tmp_path / "dispatch.db", timeout=0)

    def during_call() -> None:
        # Unlike the old test fake, this measures the lock rather than asserting
        # against the caller's in_transaction flag.
        other.execute("BEGIN IMMEDIATE")
        other.execute("UPDATE institution SET is_oauth = is_oauth")
        other.commit()
        assert other.execute("SELECT kind, ok FROM sync_run").fetchall() == [("FULL_SYNC", None)]
        assert other.execute("SELECT count(*) FROM observation").fetchone() == (0,)

    client.before = during_call
    try:
        runner = dispatcher(db, tmp_path, client)
        assert db.execute("PRAGMA busy_timeout").fetchone() == (5000,)
        result = runner.run_due()
        assert result.ok and result.state == "COMPLETED"
        assert len(client.calls) == 2
        assert other.execute("SELECT count(*) FROM observation").fetchone() == (2,)
        assert other.execute("SELECT ok FROM sync_run").fetchone() == (1,)
        assert runner.run_due().state == "NOT_DUE"
        assert len(client.calls) == 2
        assert not FullSyncSchedule(other).due(at=NOW).due
    finally:
        other.close()


def test_bad_historical_clock_still_dispatches_after_new_healthy_success(
    db: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db.execute(
        'INSERT INTO sync_run(id, started_at, finished_at, "trigger", kind, ok) '
        "VALUES ('bad-clock', 'synthetic-private-clock', ?, 'TEST', 'FULL_SYNC', 1)",
        (NOW.isoformat(),),
    )
    db.commit()
    client = Client()
    runner = dispatcher(db, tmp_path, client)
    for _ in range(2):
        result = runner.run_due()
        assert result.ok and result.schedule_state_error
    assert len(client.calls) == 4
    assert "treating full sync as due" in caplog.text
    assert "synthetic-private-clock" not in caplog.text


def test_active_transaction_and_invalid_time_never_trigger_corrupt_state_fallback(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    client = Client()
    runner = dispatcher(db, tmp_path, client)
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(ValueError, match="no active transaction"):
        runner.run_due()
    assert db.in_transaction  # The runner may not commit/rollback somebody else's work.
    db.rollback()
    runner = dispatcher(db, tmp_path, client, at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        runner.run_due()
    assert client.calls == []
    assert db.execute("SELECT count(*) FROM sync_run").fetchone() == (0,)


def test_guard_at_collection_refuses_an_uncommitted_run_before_provider_io(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Client()
    runner = dispatcher(db, tmp_path, client)

    def broken_write(operation: Callable[[], T]) -> T:
        db.execute("BEGIN IMMEDIATE")
        return operation()  # Simulate a refactor accidentally removing commit.

    monkeypatch.setattr(runner, "_write", broken_write)
    with pytest.raises(ValueError, match="no active transaction"):
        runner.run_due()
    assert client.calls == []
    db.rollback()


def test_retry_backoff_survives_reopen_caps_at_eight_hours_and_resets(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    # One Item makes the all-deferred path observable without extra healthy work.
    db.execute("UPDATE account SET archived_at = ? WHERE item_id = 2", (NOW.isoformat(),))
    db.commit()
    client = Client()
    client.fail = {"material-one"}
    at = NOW
    for failures, hours in ((1, 1), (2, 2), (3, 4), (4, 8), (4, 8)):
        with sqlite3.connect(tmp_path / "dispatch.db") as reopened:
            migrate(reopened)
            result = dispatcher(reopened, tmp_path, client, at=at).run_due()
            assert result.ok is False
            row = reopened.execute(
                "SELECT failures, next_attempt_at FROM full_sync_retry"
            ).fetchone()
            assert row is not None and row[0] == failures
            next_at = at + timedelta(hours=hours)
            assert datetime.fromisoformat(row[1]) == next_at
            call_count = len(client.calls)
            deferred = dispatcher(
                reopened, tmp_path, client, at=next_at - timedelta(microseconds=1)
            ).run_due()
            assert deferred.state == "DEFERRED"
            assert len(client.calls) == call_count
        reopened.close()
        at = next_at
    client.fail.clear()
    assert dispatcher(db, tmp_path, client, at=at).run_due().ok
    assert db.execute("SELECT * FROM full_sync_retry").fetchall() == []
    assert not FullSyncSchedule(db).due(at=at).due


def test_deferred_item_does_not_block_healthy_item_or_advance_its_retry(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    client = Client()
    runner = dispatcher(db, tmp_path, client)
    # First seed older observations, then make the run due without deleting its history.
    assert runner.run_due().ok
    later = NOW + timedelta(hours=21)
    client.fail = {"material-one"}
    assert dispatcher(db, tmp_path, client, at=later).run_due().ok is False
    before = db.execute("SELECT * FROM full_sync_retry").fetchall()
    client.calls.clear()
    result = dispatcher(db, tmp_path, client, at=later + timedelta(minutes=5)).run_due()
    assert result.ok is False
    assert client.calls == ["material-two"]
    assert db.execute("SELECT * FROM full_sync_retry").fetchall() == before
    rows = db.execute(
        "SELECT account_id, fetched_at, source_as_of, is_carried_forward "
        "FROM observation WHERE sync_run_id = ? ORDER BY account_id",
        (result.run_id,),
    ).fetchall()
    assert rows[0][1] == NOW.isoformat(timespec="microseconds").replace("+00:00", "Z")
    assert rows[0][3] == 1 and rows[1][3] == 0


def test_busy_replay_rolls_back_entire_finish_and_never_recollects(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Client()
    client.fail = {"material-one"}
    runner = dispatcher(db, tmp_path, client)
    original = runner._record_retries
    attempts = 0
    delays: list[float] = []
    monkeypatch.setattr(runner, "_sleep", delays.append)
    observer = sqlite3.connect(tmp_path / "dispatch.db")

    def persist_then_busy(plan: FullSyncPlan, at: datetime) -> None:
        nonlocal attempts
        attempts += 1
        original(plan, at)
        assert observer.execute("SELECT count(*) FROM observation").fetchone() == (0,)
        assert observer.execute("SELECT * FROM full_sync_retry").fetchall() == []
        assert observer.execute("SELECT ok FROM sync_run").fetchone() == (None,)
        if attempts < 3:
            error = sqlite3.OperationalError("synthetic busy")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
            raise error

    monkeypatch.setattr(runner, "_record_retries", persist_then_busy)
    try:
        result = runner.run_due()
        assert result.ok is False and attempts == 3
        assert len(client.calls) == 2
        assert len(delays) == 2 and 0.05 <= delays[0] <= 0.15 and 0.1 <= delays[1] <= 0.3
        assert observer.execute("SELECT count(*) FROM observation").fetchone() == (1,)
        assert observer.execute("SELECT failures FROM full_sync_retry").fetchone() == (1,)
        assert observer.execute("SELECT ok FROM sync_run").fetchone() == (0,)
    finally:
        observer.close()


@pytest.mark.parametrize("busy", [True, False])
def test_exhaustion_or_nonbusy_error_leaves_only_unfinished_run(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, busy: bool
) -> None:
    runner = dispatcher(db, tmp_path, Client())
    attempts = 0

    def fail(plan: FullSyncPlan, at: datetime) -> None:
        nonlocal attempts
        attempts += 1
        error = sqlite3.OperationalError("synthetic failure")
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY if busy else sqlite3.SQLITE_IOERR
        raise error

    monkeypatch.setattr(runner, "_record_retries", fail)
    monkeypatch.setattr(runner, "_sleep", lambda delay: None)
    with pytest.raises(sqlite3.OperationalError):
        runner.run_due()
    assert attempts == (3 if busy else 1)
    assert not db.in_transaction
    assert db.execute("SELECT finished_at, ok FROM sync_run").fetchall() == [(None, None)]
    assert db.execute("SELECT * FROM observation").fetchall() == []
    assert FullSyncSchedule(db).due(at=NOW).due


def test_actual_writer_contention_retries_admission_before_any_provider_call(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Client()
    runner = dispatcher(db, tmp_path, client)
    db.execute("PRAGMA busy_timeout = 0")  # Same busy code, without a 5-second test wait.
    other = sqlite3.connect(tmp_path / "dispatch.db")
    other.execute("BEGIN IMMEDIATE")
    waits = []

    def release(delay: float) -> None:
        waits.append(delay)
        assert not client.calls
        other.rollback()

    monkeypatch.setattr(runner, "_sleep", release)
    try:
        assert runner.run_due().ok
        assert len(waits) == 1 and len(client.calls) == 2
    finally:
        other.close()


def test_crash_before_persistence_remains_due_on_restart(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    client = Client()

    def crash() -> None:
        raise KeyboardInterrupt

    client.before = crash
    with pytest.raises(KeyboardInterrupt):
        dispatcher(db, tmp_path, client).run_due()
    reopened = sqlite3.connect(tmp_path / "dispatch.db")
    try:
        migrate(reopened)
        assert reopened.execute("SELECT ok FROM sync_run").fetchall() == [(None,)]
        assert dispatcher(reopened, tmp_path, Client()).run_due().ok
        assert reopened.execute("SELECT ok FROM sync_run ORDER BY rowid").fetchall() == [
            (None,),
            (1,),
        ]
    finally:
        reopened.close()


def test_two_dispatchers_in_one_thread_cannot_nest_under_reentrant_file_lock(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    client = Client()
    first = dispatcher(db, tmp_path, client)
    second = dispatcher(db, tmp_path, Client())
    refused = []

    def nested() -> None:
        with pytest.raises(LockUnavailable):
            second.run_due()
        refused.append(True)

    client.before = nested
    assert first.run_due().ok and len(refused) == 2


def test_lock_held_by_another_thread_prevents_even_run_creation(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    held, release = threading.Event(), threading.Event()

    def owner() -> None:
        with exclusive_file_lock(tmp_path / "sync.lock"):
            held.set()
            assert release.wait(5)

    thread = threading.Thread(target=owner)
    thread.start()
    try:
        assert held.wait(5)
        with pytest.raises(LockUnavailable):
            dispatcher(db, tmp_path, Client()).run_due()
        assert db.execute("SELECT count(*) FROM sync_run").fetchone() == (0,)
    finally:
        release.set()
        thread.join(5)
