"""Health dispatch against migrated WAL databases; all provider data synthetic."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Never

import pytest

from networth.dispatch import HealthDispatcher
from networth.filelock import LockUnavailable
from networth.item_health import ItemHealthPoller, PollBatchResult, PollPlan
from networth.model import ItemHealthUpdate, ItemState
from networth.plaid import ItemStatus
from networth.scheduling import FullSyncSchedule
from networth.storage import migrate
from networth.store import Store
from networth.sync import BalanceMode, FullSync
from tests.test_dispatch import NOW, Client, dispatcher
from tests.test_dispatch import db as db
from tests.test_item_health import item_status
from tests.test_sync import FakeTokens


class HealthClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.before: Callable[[], None] = lambda: None
        self.fail: set[str] = set()

    def item_get(self, access_token: str) -> ItemStatus:
        self.calls.append(access_token)
        self.before()
        if access_token in self.fail:
            raise RuntimeError("synthetic-private-detail")
        return item_status(access_token.removeprefix("material-"), ItemState.HEALTHY)


def health(db: sqlite3.Connection, tmp_path: Path, client: HealthClient) -> HealthDispatcher:
    return HealthDispatcher(
        db, client, FakeTokens(), lock_path=tmp_path / "sync.lock", clock=lambda: NOW
    )


def test_health_frees_writer_and_due_clock_survives_restart(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    client = HealthClient()
    observer = sqlite3.connect(tmp_path / "dispatch.db", timeout=0)

    def independent_writer() -> None:
        observer.execute("BEGIN IMMEDIATE")
        observer.execute("UPDATE institution SET is_oauth = is_oauth")
        observer.commit()
        assert observer.execute("SELECT last_health_poll_at FROM item").fetchall() == [
            (None,),
            (None,),
        ]

    client.before = independent_writer
    try:
        result = health(db, tmp_path, client).run_due()
        assert result.ok and result.recorded_count == result.attempted_count == 2
        assert client.calls == ["material-one", "material-two"]
        assert all(
            row[0] is not None for row in observer.execute("SELECT last_health_poll_at FROM item")
        )
        assert observer.execute("SELECT * FROM sync_run").fetchall() == []
        assert FullSyncSchedule(db).due(at=NOW).due
    finally:
        observer.close()
    client.before = lambda: None
    with sqlite3.connect(tmp_path / "dispatch.db") as reopened:
        migrate(reopened)
        runner = health(reopened, tmp_path, client)
        runner._clock = lambda: NOW + timedelta(hours=1, microseconds=-1)
        assert runner.run_due().attempted_count == 0
        assert client.calls == ["material-one", "material-two"]
        runner._clock = lambda: NOW + timedelta(hours=1)
        assert runner.run_due().recorded_count == 2
        assert client.calls == ["material-one", "material-two"] * 2
    reopened.close()


def test_failed_health_target_stays_due_without_discarding_other_observation(
    db: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client = HealthClient()
    client.fail.add("material-one")
    runner = health(db, tmp_path, client)
    first = runner.run_due()
    assert not first.ok and first.attempted_count == 2 and first.recorded_count == 1
    assert first.failure_types == ("RuntimeError",)
    assert db.execute("SELECT last_health_poll_at FROM item WHERE id = 1").fetchone() == (None,)
    assert db.execute("SELECT last_health_poll_at FROM item WHERE id = 2").fetchone() != (None,)
    assert not runner.run_due().ok
    assert client.calls == ["material-one", "material-two", "material-one"]
    assert "synthetic-private-detail" not in caplog.text
    client.fail.clear()
    assert runner.run_due().recorded_count == 1
    assert runner.run_due().attempted_count == 0


def test_health_busy_replays_only_atomic_persistence_and_keeps_newer_poll(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = HealthClient()
    runner = health(db, tmp_path, client)
    persist = runner._worker.persist
    observer = sqlite3.connect(tmp_path / "dispatch.db", timeout=0)
    migrate(observer)
    attempts = 0
    waits: list[float] = []

    def busy_after_writes(plan: PollPlan) -> PollBatchResult:
        nonlocal attempts
        attempts += 1
        # The explicit write reservation must exist before the first write.
        # An implicit transaction opened by record_poll is too late.
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            observer.execute("BEGIN IMMEDIATE")
        result = persist(plan)
        if attempts == 1:
            assert observer.execute("SELECT last_health_poll_at FROM item").fetchall() == [
                (None,),
                (None,),
            ]
            error = sqlite3.OperationalError("synthetic busy")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
            raise error
        return result

    def after_rollback(delay: float) -> None:
        waits.append(delay)
        assert not db.in_transaction
        assert db.execute("SELECT last_health_poll_at FROM item").fetchall() == [(None,), (None,)]
        Store(observer).items.record_poll(
            1,
            ItemHealthUpdate(
                polled_at=NOW + timedelta(minutes=1),
                status=ItemState.REVOKED,
                error_code="ITEM_NOT_FOUND",
                error_detail="Synthetic revoked Item",
                investments_status_observed=False,
                investments_last_successful_update=None,
            ),
        )
        observer.commit()

    monkeypatch.setattr(runner._worker, "persist", busy_after_writes)
    monkeypatch.setattr(runner, "_sleep", after_rollback)
    try:
        assert runner.run_due().ok
        assert attempts == 2 and len(waits) == 1 and 0.05 <= waits[0] <= 0.15
        assert client.calls == ["material-one", "material-two"]
        first, second = Store(db).items.all()
        assert first.status is ItemState.REVOKED
        assert second.last_polled_at == NOW
    finally:
        observer.close()


@pytest.mark.parametrize("busy", [True, False])
def test_health_write_failure_rolls_back_and_remains_due(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, busy: bool
) -> None:
    client = HealthClient()
    runner = health(db, tmp_path, client)
    persist = runner._worker.persist
    attempts = 0

    def fail_after_writes(plan: PollPlan) -> PollBatchResult:
        nonlocal attempts
        attempts += 1
        persist(plan)
        error = sqlite3.OperationalError("synthetic failure")
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY if busy else sqlite3.SQLITE_IOERR
        raise error

    monkeypatch.setattr(runner._worker, "persist", fail_after_writes)
    monkeypatch.setattr(runner, "_sleep", lambda delay: None)
    with pytest.raises(sqlite3.OperationalError):
        runner.run_due()
    assert attempts == (3 if busy else 1)
    assert client.calls == ["material-one", "material-two"]
    assert not db.in_transaction
    assert db.execute("SELECT last_health_poll_at FROM item").fetchall() == [(None,), (None,)]
    assert health(db, tmp_path, HealthClient()).run_due().recorded_count == 2


@pytest.mark.parametrize(
    "method",
    ["collect_due", "collect_all", "poll_due", "poll_all", "poll_item", "sync_collect", "sync_run"],
)
@pytest.mark.parametrize("begin", ["BEGIN", "BEGIN IMMEDIATE"])
def test_workers_refuse_caller_transaction_before_tokens_or_provider(
    db: sqlite3.Connection, method: str, begin: str
) -> None:
    class Tokens(FakeTokens):
        def get(self, secret_ref: str) -> Never:
            pytest.fail("resolved a token inside caller transaction")

    tokens = Tokens()
    client = HealthClient()
    sync_client = Client()
    poller = ItemHealthPoller(Store(db).items, client, tokens)
    sync = FullSync(Store(db), sync_client, tokens, balance_mode=BalanceMode.REALTIME)
    db.execute(begin)
    with pytest.raises(ValueError, match="no active transaction"):
        if method == "sync_collect":
            sync.collect("synthetic-run", at=NOW)
        elif method == "sync_run":
            sync.run("synthetic-run", at=NOW)
        elif method == "poll_item":
            poller.poll_item(1, at=NOW)
        else:
            getattr(poller, method)(at=NOW)
    assert db.in_transaction
    assert client.calls == [] and sync_client.calls == []
    db.rollback()


def test_health_and_full_sync_share_admission_lock(db: sqlite3.Connection, tmp_path: Path) -> None:
    client = HealthClient()
    full_client = Client()
    runner = health(db, tmp_path, client)
    full = dispatcher(db, tmp_path, full_client)

    def nested_full() -> None:
        with pytest.raises(LockUnavailable):
            full.run_due()
        assert full_client.calls == []

    client.before = nested_full
    assert runner.run_due().ok

    def nested_health() -> None:
        with pytest.raises(LockUnavailable):
            runner.run_due()
        assert client.calls == ["material-one", "material-two"]

    full_client.before = nested_health
    assert full.run_due().ok


def test_health_crash_and_caller_misuse_do_not_consume_due_work(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    client = HealthClient()
    runner = health(db, tmp_path, client)
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(ValueError, match="no active transaction"):
        runner.run_due()
    assert db.in_transaction and client.calls == []
    db.rollback()
    runner._clock = lambda: NOW.replace(tzinfo=None)
    with pytest.raises(ValueError):
        runner.run_due()
    assert client.calls == []
    runner._clock = lambda: NOW

    def crash_on_second() -> None:
        if len(client.calls) == 2:
            raise KeyboardInterrupt

    client.before = crash_on_second
    with pytest.raises(KeyboardInterrupt):
        runner.run_due()
    assert db.execute("SELECT last_health_poll_at FROM item").fetchall() == [(None,), (None,)]
    with sqlite3.connect(tmp_path / "dispatch.db") as reopened:
        migrate(reopened)
        assert health(reopened, tmp_path, HealthClient()).run_due().recorded_count == 2
    reopened.close()
