"""Quote-only cycles with real WAL, source clocks and transaction failures."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from networth.alerts import AlertEvaluation
from networth.filelock import LockUnavailable, exclusive_file_lock
from networth.manual_quotes import ManualQuoteInputError
from networth.model import AlertKind, Snapshot
from networth.payload import open_payload
from networth.publisher import Publisher
from networth.quote_cycle import QuoteCycleDispatcher
from networth.quote_scheduling import QuoteRefreshSchedule, QuoteScheduleStateError
from networth.scheduling import FullSyncSchedule
from networth.snapshotter import SnapshotInputError, Snapshotter
from networth.storage import migrate
from networth.store import Store
from tests.test_dispatch import NOW
from tests.test_full_cycle import Client, fresh_quotes, runner
from tests.test_full_cycle import db as db
from tests.test_manual_quotes import Quotes

AT = NOW + timedelta(minutes=1)


def seed(db: sqlite3.Connection, tmp_path: Path) -> str:
    quotes = fresh_quotes()
    quotes.result["SYNTH"] = replace(quotes.result["SYNTH"], as_of=NOW - timedelta(hours=2))
    result = runner(db, tmp_path, Client(), quotes).run_due()
    assert result.ok and result.run_id is not None
    assert QuoteRefreshSchedule(db).due(at=AT).due
    return result.run_id


def dispatcher(db: sqlite3.Connection, tmp_path: Path, quotes: Quotes) -> QuoteCycleDispatcher:
    return QuoteCycleDispatcher(
        db, quotes, lock_path=tmp_path / "sync.lock", clock=lambda: AT, sleep=lambda _: None
    )


def test_quote_cycle_preserves_linked_age_and_full_due_clock(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    original = seed(db, tmp_path)
    store = Store(db)
    old = next(o for o in store.observations.for_sync_run(original) if o.account_id == 1)
    summary = db.execute(
        "SELECT last_fetch_at, last_source_as_of FROM account WHERE id=1"
    ).fetchone()
    full_due = FullSyncSchedule(db).due(at=AT)
    quotes = fresh_quotes()
    quotes.result["SYNTH"] = replace(quotes.result["SYNTH"], price=Decimal("30"))
    rival = sqlite3.connect(tmp_path / "cycle.db", timeout=0)
    migrate(rival)

    def during_io() -> None:
        assert not db.in_transaction
        rival.execute("BEGIN IMMEDIATE")
        rival.execute("UPDATE institution SET is_oauth=is_oauth")
        rival.commit()
        assert rival.execute("SELECT kind, ok FROM sync_run ORDER BY rowid").fetchall() == [
            ("FULL_SYNC", 1),
            ("OTHER", None),
        ]
        with pytest.raises(LockUnavailable):
            dispatcher(rival, tmp_path, fresh_quotes()).run_due()

    quotes.before = during_io
    try:
        outcome = dispatcher(db, tmp_path, quotes).run_due()
        assert outcome.ok and outcome.run_id is not None
        assert quotes.calls == [("SYNTH",)]
        values = store.observations.for_sync_run(outcome.run_id)
        carried = next(o for o in values if o.account_id == old.account_id)
        assert carried.figure == old.figure and carried.source == old.source
        assert carried.fetched_at == old.fetched_at and carried.observed_at == AT
        assert carried.is_carried_forward
        assert (
            db.execute("SELECT last_fetch_at, last_source_as_of FROM account WHERE id=1").fetchone()
            == summary
        )
        assert FullSyncSchedule(db).due(at=AT) == full_due
        assert not QuoteRefreshSchedule(rival).due(at=AT).due
        snapshot = Store(rival).snapshots.for_sync_run(outcome.run_id)
        assert snapshot is not None and not snapshot.is_complete
        assert snapshot.net_worth.value_minor == sum(o.figure.value_minor for o in values)
        assert snapshot.net_worth.value_minor == old.figure.value_minor + 6000
        assert snapshot.age.as_of == NOW - timedelta(hours=1)
        assert [a.kind for a in store.alerts.open()] == [AlertKind.SHARE_COUNT_UNCONFIRMED]
        assert db.execute("SELECT count(*) FROM full_sync_retry").fetchone() == (0,)
        db.execute(
            "INSERT INTO pairing(id, created_at, key_ref, state) "
            "VALUES ('00000000-0000-4000-8000-000000000016', ?, 'synthetic', 'ACTIVE')",
            (AT.isoformat(),),
        )
        db.commit()
        key = bytes(range(32))
        payload = json.loads(
            open_payload(Publisher(db, lambda _: key).publish(at=AT).envelope, key)
        )
        assert payload["total"]["value_minor"] == snapshot.net_worth.value_minor
        assert payload["total"]["as_of"] == "2026-09-18T20:00:00.000000Z"
    finally:
        rival.close()


def test_old_quote_success_stays_due_after_restart(db: sqlite3.Connection, tmp_path: Path) -> None:
    seed(db, tmp_path)
    quotes = fresh_quotes()
    quotes.result["SYNTH"] = replace(quotes.result["SYNTH"], as_of=NOW - timedelta(hours=2))
    assert dispatcher(db, tmp_path, quotes).run_due().ok
    reopened = sqlite3.connect(tmp_path / "cycle.db")
    migrate(reopened)
    try:
        assert QuoteRefreshSchedule(reopened).due(at=AT).due
        assert dispatcher(reopened, tmp_path, fresh_quotes()).run_due().ok
        assert not QuoteRefreshSchedule(reopened).due(at=AT).due
    finally:
        reopened.close()


@pytest.mark.parametrize("stage", ["missing_quote", "manual_edit", "snapshot", "alerts"])
def test_failure_keeps_cycle_unfinished_and_due(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    seed(db, tmp_path)
    before = {
        table: db.execute(f"SELECT * FROM {table}").fetchall()
        for table in ("observation", "snapshot", "alert", "full_sync_retry")
    }
    quotes = fresh_quotes()
    dispatch = dispatcher(db, tmp_path, quotes)
    if stage == "missing_quote":
        quotes.result.clear()
    elif stage == "manual_edit":

        def edit() -> None:
            with sqlite3.connect(tmp_path / "cycle.db") as rival:
                rival.execute("UPDATE manual_asset SET share_count='3'")
            rival.close()

        quotes.before = edit
    elif stage == "snapshot":
        original = type(dispatch._snapshotter).run

        def fail_snapshot(self: Snapshotter, run_id: str, *, at: datetime) -> Snapshot:
            original(self, run_id, at=at)
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(type(dispatch._snapshotter), "run", fail_snapshot)
    else:
        original_alerts = dispatch._alerts.evaluate

        def fail_alerts(*, at: datetime) -> AlertEvaluation:
            original_alerts(at=at)
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(dispatch._alerts, "evaluate", fail_alerts)
    with pytest.raises((ManualQuoteInputError, RuntimeError)):
        dispatch.run_due()
    assert db.execute("SELECT ok, finished_at FROM sync_run ORDER BY rowid").fetchall()[-1] == (
        None,
        None,
    )
    for table, rows in before.items():
        assert db.execute(f"SELECT * FROM {table}").fetchall() == rows
    assert QuoteRefreshSchedule(db).due(at=AT).due and not db.in_transaction


def test_busy_completion_rolls_back_and_replays_without_refetch(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(db, tmp_path)
    quotes = fresh_quotes()
    dispatch = dispatcher(db, tmp_path, quotes)
    original = dispatch._alerts.evaluate
    attempts: list[datetime] = []
    rival = sqlite3.connect(tmp_path / "cycle.db")

    def busy_once(*, at: datetime) -> AlertEvaluation:
        result = original(at=at)
        attempts.append(at)
        assert rival.execute("SELECT count(*) FROM snapshot").fetchone() == (1,)
        assert rival.execute("SELECT ok FROM sync_run ORDER BY rowid").fetchall() == [(1,), (None,)]
        if len(attempts) == 1:
            exc = sqlite3.OperationalError("synthetic busy")
            exc.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise exc
        return result

    monkeypatch.setattr(dispatch._alerts, "evaluate", busy_once)
    try:
        assert dispatch.run_due().ok
        assert len(attempts) == 2 and quotes.calls == [("SYNTH",)]
        assert rival.execute("SELECT count(*) FROM snapshot").fetchone() == (2,)
        assert rival.execute("SELECT count(*) FROM observation").fetchone() == (4,)
    finally:
        rival.close()


def test_idle_admission_needs_no_writer_and_makes_no_run(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    runner(db, tmp_path, Client(), fresh_quotes()).run_due()
    quotes = fresh_quotes()
    dispatch = dispatcher(db, tmp_path, quotes)
    db.execute("PRAGMA busy_timeout=0")
    rival = sqlite3.connect(tmp_path / "cycle.db")
    rival.execute("BEGIN IMMEDIATE")
    try:
        assert dispatch.run_due().state == "NOT_DUE"
        assert not quotes.calls
        assert db.execute("SELECT count(*) FROM sync_run").fetchone() == (1,)
    finally:
        rival.rollback()
        rival.close()


@pytest.mark.parametrize("state", ["CONFIRMED", "NEW"])
def test_missing_linked_value_refuses_unless_excluded_as_new(
    db: sqlite3.Connection, tmp_path: Path, state: str
) -> None:
    # No full cycle has run, so the linked account has no observation to carry.
    db.execute("UPDATE account SET reconciliation_state=? WHERE id=1", (state,))
    db.commit()
    dispatch = dispatcher(db, tmp_path, fresh_quotes())
    if state == "NEW":
        result = dispatch.run_due()
        assert result.ok and result.run_id is not None
        snapshot = Store(db).snapshots.for_sync_run(result.run_id)
        assert snapshot is not None and snapshot.counts.unreconciled_account_count == 1
    else:
        with pytest.raises(SnapshotInputError, match="no linked value"):
            dispatch.run_due()
        assert db.execute("SELECT count(*) FROM observation").fetchone() == (0,)


def test_invalid_price_evidence_refuses_before_run_and_fetch(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    seed(db, tmp_path)
    quotes = fresh_quotes()
    dispatch = dispatcher(db, tmp_path, quotes)
    dispatch._clock = lambda: NOW - timedelta(seconds=1)
    with pytest.raises(QuoteScheduleStateError):
        dispatch.run_due()
    assert not quotes.calls and not db.in_transaction
    assert db.execute("SELECT count(*) FROM sync_run").fetchone() == (1,)


def test_canonical_lock_and_active_transaction_refuse_before_io(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    quotes = fresh_quotes()
    dispatch = dispatcher(db, tmp_path, quotes)
    with (
        exclusive_file_lock(tmp_path / "sync.lock", blocking=False, reentrant=False),
        pytest.raises(LockUnavailable),
    ):
        dispatch.run_due()
    db.execute("BEGIN")
    with pytest.raises(ValueError, match="active transaction"):
        dispatch.run_due()
    db.rollback()
    assert not quotes.calls
    assert db.execute("SELECT count(*) FROM sync_run").fetchone() == (0,)


def test_manual_only_cycle_never_satisfies_full_sync(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    db.execute("UPDATE account SET include_in_net_worth=0 WHERE id=1")
    db.commit()
    assert dispatcher(db, tmp_path, fresh_quotes()).run_due().ok
    assert FullSyncSchedule(db).due(at=AT).due
    assert not QuoteRefreshSchedule(db).due(at=AT).due


def test_latest_linked_value_committed_during_quote_io_is_carried(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    original = seed(db, tmp_path)
    old = Store(db).observations.for_sync_run(original)[0]
    quotes = fresh_quotes()
    updated = replace(
        old.as_draft(),
        sync_run_id="independent",
        observed_at=AT,
        figure=replace(old.figure, value_minor=old.figure.value_minor + 123),
    )

    def edit() -> None:
        rival = sqlite3.connect(tmp_path / "cycle.db")
        migrate(rival)
        try:
            with rival:
                rival.execute(
                    'INSERT INTO sync_run(id, started_at, "trigger", kind) '
                    "VALUES ('independent', ?, 'SYNTHETIC', 'OTHER')",
                    (AT.isoformat(),),
                )
                Store(rival).observations.append(updated)
        finally:
            rival.close()

    quotes.before = edit
    result = dispatcher(db, tmp_path, quotes).run_due()
    assert result.run_id is not None
    carried = next(
        o
        for o in Store(db).observations.for_sync_run(result.run_id)
        if o.account_id == old.account_id
    )
    assert carried.figure == updated.figure and carried.fetched_at == old.fetched_at
    assert carried.is_carried_forward


def test_no_manual_targets_does_not_create_run_or_fetch(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    db.execute("UPDATE account SET include_in_net_worth=0 WHERE item_id IS NULL")
    db.commit()
    quotes = fresh_quotes()
    assert dispatcher(db, tmp_path, quotes).run_due().state == "NOT_DUE"
    assert quotes.calls == [] and db.execute("SELECT count(*) FROM sync_run").fetchone() == (0,)


def test_completion_clock_regression_keeps_cycle_unfinished(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    seed(db, tmp_path)
    dispatch = dispatcher(db, tmp_path, fresh_quotes())
    clock = iter((AT, AT - timedelta(seconds=1)))
    dispatch._clock = lambda: next(clock)
    with pytest.raises(ValueError, match="completion precedes"):
        dispatch.run_due()
    assert db.execute("SELECT ok, finished_at FROM sync_run ORDER BY rowid").fetchall()[-1] == (
        None,
        None,
    )
    assert QuoteRefreshSchedule(db).due(at=AT).due
