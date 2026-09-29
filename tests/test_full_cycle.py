"""Full-cycle completion against real workers, WAL storage and encrypted payloads."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from networth.alerts import AlertEvaluation
from networth.cycle_alerts import CycleAlertInputError
from networth.filelock import LockUnavailable, exclusive_file_lock
from networth.full_cycle import FullCycleDispatcher
from networth.manual_quotes import ManualQuoteInputError
from networth.model import AlertKind, FreshnessPolicy, Quote, Snapshot
from networth.payload import open_payload
from networth.plaid import BalanceRecord
from networth.publisher import PairingUnavailableError, Publisher
from networth.scheduling import FullSyncSchedule
from networth.snapshotter import SnapshotInputError
from networth.storage import migrate
from networth.store import Store
from networth.sync import BalanceMode
from tests.test_dispatch import NOW
from tests.test_dispatch import Client as BaseClient
from tests.test_manual_quotes import Quotes
from tests.test_sync import FakeTokens, add_account, add_item


class Client(BaseClient):
    def fetch_realtime_balances(self, access_token: str) -> tuple[BalanceRecord, ...]:
        return tuple(
            replace(record, last_updated_datetime=NOW)
            for record in super().fetch_realtime_balances(access_token)
        )


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(tmp_path / "cycle.db")
    migrate(connection)
    connection.execute(
        "INSERT INTO institution(plaid_institution_id, name, is_oauth) "
        "VALUES ('synthetic-institution', 'Synthetic institution', 0)"
    )
    add_account(connection, add_item(connection, "one"), "one")
    manual = add_account(connection, None, "manual", policy=FreshnessPolicy.MANUAL_QTY_LIVE_PRICE)
    connection.execute(
        "INSERT INTO manual_asset(account_id, kind, symbol, share_count, valued_as_of) "
        "VALUES (?, 'EQUITY_SHARES', 'SYNTH', '2', ?)",
        (manual, (NOW - timedelta(days=100)).isoformat()),
    )
    connection.commit()
    yield connection
    connection.close()


def runner(
    db: sqlite3.Connection,
    tmp_path: Path,
    client: Client,
    quotes: Quotes,
    *,
    at: datetime = NOW,
) -> FullCycleDispatcher:
    return FullCycleDispatcher(
        db,
        client,
        FakeTokens(),
        quotes,
        balance_mode=BalanceMode.REALTIME,
        lock_path=tmp_path / "sync.lock",
        clock=lambda: at,
        sleep=lambda _: None,
    )


def fresh_quotes() -> Quotes:
    quotes = Quotes()
    quotes.result = {"SYNTH": Quote("SYNTH", Decimal("12.50"), "USD", NOW - timedelta(hours=1))}
    return quotes


def assert_unfinished(db: sqlite3.Connection) -> None:
    assert db.execute("SELECT ok, finished_at FROM sync_run").fetchall() == [(None, None)]
    for table in ("observation", "snapshot", "alert", "full_sync_retry"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
    assert FullSyncSchedule(db).due(at=NOW).due
    assert not db.in_transaction


def test_cycle_collects_without_writer_then_atomically_snapshots_and_publishes(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    client, quotes = Client(), fresh_quotes()
    rival = sqlite3.connect(tmp_path / "cycle.db", timeout=0)
    migrate(rival)

    def during_io() -> None:
        rival.execute("BEGIN IMMEDIATE")
        rival.execute("UPDATE institution SET is_oauth = is_oauth")
        rival.commit()
        assert rival.execute("SELECT kind, ok FROM sync_run").fetchall() == [("FULL_SYNC", None)]
        assert rival.execute("SELECT count(*) FROM observation").fetchone() == (0,)
        with pytest.raises(LockUnavailable):
            runner(rival, tmp_path, Client(), fresh_quotes()).run_due()

    client.before = quotes.before = during_io
    try:
        result = runner(db, tmp_path, client, quotes).run_due()
        assert result.ok and result.run_id is not None
        assert client.calls == ["material-one"] and quotes.calls == [("SYNTH",)]
        observations = Store(rival).observations.for_sync_run(result.run_id)
        assert len(observations) == 2
        snapshot = Store(rival).snapshots.for_sync_run(result.run_id)
        assert snapshot is not None
        assert snapshot.net_worth.value_minor == sum(o.figure.value_minor for o in observations)
        assert snapshot.age.as_of == NOW - timedelta(hours=1)
        assert not FullSyncSchedule(rival).due(at=NOW).due
        assert [a.kind for a in Store(rival).alerts.open()] == [AlertKind.SHARE_COUNT_UNCONFIRMED]
        assert rival.execute("SELECT notified_at FROM alert").fetchall() == [(None,)]
        db.execute(
            "INSERT INTO pairing(id, created_at, key_ref, state) "
            "VALUES ('00000000-0000-4000-8000-000000000016', ?, 'synthetic', 'ACTIVE')",
            (NOW.isoformat(),),
        )
        db.commit()
        key = bytes(range(32))
        payload = json.loads(
            open_payload(Publisher(db, lambda _: key).publish(at=NOW).envelope, key)
        )
        assert payload["total"]["value_minor"] == snapshot.net_worth.value_minor
        assert payload["total"]["as_of"] == "2026-09-18T20:00:00.000000Z"
        assert [a["kind"] for a in payload["alerts"]] == ["SHARE_COUNT_UNCONFIRMED"]
    finally:
        rival.close()


def test_missing_quote_leaves_no_completed_cycle_and_restart_recollects(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    client, quotes = Client(), fresh_quotes()
    quotes.result.clear()
    with pytest.raises(ManualQuoteInputError):
        runner(db, tmp_path, client, quotes).run_due()
    assert_unfinished(db)
    reopened = sqlite3.connect(tmp_path / "cycle.db")
    migrate(reopened)
    try:
        assert runner(reopened, tmp_path, client, fresh_quotes()).run_due().ok
        assert reopened.execute("SELECT ok FROM sync_run ORDER BY rowid").fetchall() == [
            (None,),
            (1,),
        ]
        assert len(client.calls) == 2
        assert reopened.execute("SELECT count(*) FROM snapshot").fetchone() == (1,)
    finally:
        reopened.close()


def test_manual_edit_during_io_rolls_back_plaid_completion_too(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    quotes = fresh_quotes()

    def edit() -> None:
        other = sqlite3.connect(tmp_path / "cycle.db")
        with other:
            other.execute("UPDATE manual_asset SET share_count = '3'")
        other.close()

    quotes.before = edit
    with pytest.raises(ManualQuoteInputError, match="changed"):
        runner(db, tmp_path, Client(), quotes).run_due()
    assert_unfinished(db)


@pytest.mark.parametrize("stage", ["snapshot", "alerts"])
def test_late_failure_rolls_back_success_observations_snapshot_and_alerts(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    dispatch = runner(db, tmp_path, Client(), fresh_quotes())
    if stage == "snapshot":
        original_snapshot = dispatch._snapshotter.run

        def fail_snapshot(sync_run_id: str, *, at: datetime) -> Snapshot:
            original_snapshot(sync_run_id, at=at)
            raise RuntimeError("synthetic snapshot failure")

        monkeypatch.setattr(
            type(dispatch._snapshotter), "run", lambda self, *a, **kw: fail_snapshot(*a, **kw)
        )
    else:
        original_alerts = dispatch._alerts.evaluate

        def fail_alerts(*, at: datetime) -> AlertEvaluation:
            original_alerts(at=at)
            raise RuntimeError("synthetic alert failure")

        monkeypatch.setattr(dispatch._alerts, "evaluate", fail_alerts)
    with pytest.raises(RuntimeError, match="synthetic"):
        dispatch.run_due()
    assert_unfinished(db)


def test_snapshot_input_refusal_does_not_consume_full_due_time(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    add_account(db, None, "missing-property", policy=FreshnessPolicy.MANUAL_STATIC)
    db.commit()
    with pytest.raises(SnapshotInputError, match="no valuation"):
        runner(db, tmp_path, Client(), fresh_quotes()).run_due()
    assert_unfinished(db)


def test_bad_alert_clock_rolls_back_snapshot_and_success(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    db.execute(
        "UPDATE item SET status_since = ?",
        ((NOW + timedelta(days=1)).isoformat().replace("+00:00", "Z"),),
    )
    db.commit()
    with pytest.raises(CycleAlertInputError):
        runner(db, tmp_path, Client(), fresh_quotes()).run_due()
    assert_unfinished(db)


def test_busy_at_last_stage_replays_only_persistence_and_exposes_no_partial_cycle(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, quotes = Client(), fresh_quotes()
    dispatch = runner(db, tmp_path, client, quotes)
    original = dispatch._alerts.evaluate
    rival = sqlite3.connect(tmp_path / "cycle.db")
    attempts = 0

    def evaluate(*, at: datetime) -> AlertEvaluation:
        nonlocal attempts
        attempts += 1
        result = original(at=at)
        assert_unfinished(rival)
        if attempts < 3:
            error = sqlite3.OperationalError("synthetic busy")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
            raise error
        return result

    monkeypatch.setattr(dispatch._alerts, "evaluate", evaluate)
    try:
        assert dispatch.run_due().ok
        assert attempts == 3
        assert client.calls == ["material-one"] and quotes.calls == [("SYNTH",)]
        assert rival.execute("SELECT count(*) FROM snapshot").fetchone() == (1,)
        assert rival.execute("SELECT count(*) FROM observation").fetchone() == (2,)
        assert rival.execute("SELECT count(*) FROM alert").fetchone() == (1,)
        assert rival.execute("SELECT ok FROM sync_run").fetchone() == (1,)
    finally:
        rival.close()


def test_failed_plaid_preserves_retry_and_alerts_without_quotes_or_snapshot(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    client, quotes = Client(), fresh_quotes()
    client.fail.add("material-one")
    quotes.result.clear()  # An unrelated quote outage cannot erase the Item retry.
    result = runner(db, tmp_path, client, quotes).run_due()
    assert result.state == "COMPLETED" and result.ok is False
    assert quotes.calls == []
    assert db.execute("SELECT failures FROM full_sync_retry").fetchall() == [(1,)]
    assert db.execute("SELECT count(*) FROM snapshot").fetchone() == (0,)
    assert [a.kind for a in Store(db).alerts.open()] == [AlertKind.SHARE_COUNT_UNCONFIRMED]
    assert FullSyncSchedule(db).due(at=NOW).due
    db.execute("UPDATE manual_asset SET valued_as_of = ?", (NOW.isoformat(),))
    db.commit()
    deferred = runner(db, tmp_path, client, quotes).run_due()
    assert deferred.state == "DEFERRED"
    assert Store(db).alerts.open() == ()  # Deferred work still observes the confirmation.
    assert len(client.calls) == 1 and quotes.calls == []
    assert db.execute("SELECT count(*) FROM sync_run").fetchone() == (1,)


def test_not_due_reassesses_alerts_without_providers_or_another_snapshot(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    client, quotes = Client(), fresh_quotes()
    assert runner(db, tmp_path, client, quotes).run_due().ok
    db.execute("UPDATE manual_asset SET valued_as_of = ?", (NOW.isoformat(),))
    db.commit()
    assert runner(db, tmp_path, client, quotes).run_due().state == "NOT_DUE"
    assert Store(db).alerts.open() == ()
    assert len(client.calls) == len(quotes.calls) == 1
    assert db.execute("SELECT count(*) FROM snapshot").fetchone() == (1,)


def test_manual_only_full_cycle_still_gets_quotes_and_snapshot(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    db.execute("UPDATE account SET archived_at = ? WHERE item_id IS NOT NULL", (NOW.isoformat(),))
    db.commit()
    client, quotes = Client(), fresh_quotes()
    result = runner(db, tmp_path, client, quotes).run_due()
    assert result.ok and result.run_id is not None
    assert client.calls == [] and quotes.calls == [("SYNTH",)]
    snapshot = Store(db).snapshots.for_sync_run(result.run_id)
    assert snapshot is not None and snapshot.net_worth.value_minor == 2500


def test_publication_failure_does_not_undo_cycle_or_repeat_collection(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    client, quotes = Client(), fresh_quotes()
    assert runner(db, tmp_path, client, quotes).run_due().ok
    with pytest.raises(PairingUnavailableError):
        Publisher(db, lambda _: bytes(range(32))).publish(at=NOW)
    assert not FullSyncSchedule(db).due(at=NOW).due
    assert runner(db, tmp_path, client, quotes).run_due().state == "NOT_DUE"
    assert len(client.calls) == len(quotes.calls) == 1
    assert db.execute("SELECT count(*) FROM snapshot").fetchone() == (1,)
    assert db.execute("SELECT count(*) FROM publication").fetchone() == (0,)


def test_lock_and_transaction_misuse_refuse_before_either_provider(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    client, quotes = Client(), fresh_quotes()
    dispatch = runner(db, tmp_path, client, quotes)
    with exclusive_file_lock(tmp_path / "sync.lock"), pytest.raises(LockUnavailable):
        dispatch.run_due()
    db.execute("BEGIN")
    with pytest.raises(ValueError, match="no active transaction"):
        dispatch.run_due()
    db.rollback()
    assert client.calls == [] and quotes.calls == []
    assert db.execute("SELECT count(*) FROM sync_run").fetchone() == (0,)


def test_completion_clock_includes_quote_io(db: sqlite3.Connection, tmp_path: Path) -> None:
    quotes = fresh_quotes()
    quotes.result["SYNTH"] = replace(quotes.result["SYNTH"], as_of=NOW + timedelta(seconds=1))
    times = iter((NOW, NOW + timedelta(seconds=2), NOW + timedelta(seconds=3)))
    dispatch = FullCycleDispatcher(
        db,
        Client(),
        FakeTokens(),
        quotes,
        balance_mode=BalanceMode.REALTIME,
        lock_path=tmp_path / "sync.lock",
        clock=lambda: next(times),
    )
    result = dispatch.run_due()
    assert result.ok and result.run_id is not None
    finished = db.execute("SELECT finished_at FROM sync_run").fetchone()[0]
    assert datetime.fromisoformat(finished) == NOW + timedelta(seconds=3)
    observation = Store(db).observations.latest_for_account(2)
    assert observation is not None
    assert observation.fetched_at == NOW + timedelta(seconds=2)
    assert observation.figure.as_of == NOW + timedelta(seconds=1)
    snapshot = Store(db).snapshots.for_sync_run(result.run_id)
    assert snapshot is not None and snapshot.taken_at == NOW + timedelta(seconds=3)


@pytest.mark.parametrize("at", [NOW - timedelta(seconds=1), NOW.replace(tzinfo=None)])
def test_invalid_completion_clock_leaves_run_unfinished(
    db: sqlite3.Connection, tmp_path: Path, at: datetime
) -> None:
    times = iter((NOW, NOW, at))
    dispatch = FullCycleDispatcher(
        db,
        Client(),
        FakeTokens(),
        fresh_quotes(),
        balance_mode=BalanceMode.REALTIME,
        lock_path=tmp_path / "sync.lock",
        clock=lambda: next(times),
    )
    message = (
        "cycle completion precedes its start" if at.tzinfo is not None else "cycle completion time"
    )
    with pytest.raises(ValueError, match=message):
        dispatch.run_due()
    assert_unfinished(db)


def test_idle_bad_clock_refuses_before_writer_admission(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    assert runner(db, tmp_path, Client(), fresh_quotes()).run_due().ok
    dispatch = runner(db, tmp_path, Client(), fresh_quotes())
    times = iter((NOW, NOW.replace(tzinfo=None)))
    dispatch._clock = lambda: next(times)
    db.execute("PRAGMA busy_timeout = 0")
    rival = sqlite3.connect(tmp_path / "cycle.db", timeout=0)
    try:
        rival.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="alert evaluation time"):
            dispatch.run_due()
        assert not db.in_transaction
    finally:
        rival.rollback()
        rival.close()
    assert db.execute("SELECT count(*) FROM sync_run").fetchone() == (1,)
