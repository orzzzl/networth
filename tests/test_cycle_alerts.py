"""Task 16 alert assembly against migrated WAL storage; synthetic data only."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from networth.alerts import AlertEvaluation
from networth.cycle_alerts import CycleAlertEvaluator
from networth.dispatch import AlertDispatcher
from networth.filelock import LockUnavailable, exclusive_file_lock
from networth.model import (
    MANUAL_VALUED_AS_OF,
    AlertKind,
    FreshnessPolicy,
    ItemState,
    ObservationSource,
)
from networth.payload import open_payload
from networth.publisher import Publisher
from networth.snapshotter import Snapshotter
from networth.storage import migrate
from networth.store import Store
from tests.test_snapshotter import NOW, add_account, add_item, add_observation, add_run


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(tmp_path / "alerts.db")
    migrate(connection)
    add_run(connection, "cycle")
    healthy = add_item(connection, "healthy")
    add_item(connection, "reauth", status=ItemState.NEEDS_REAUTH)
    add_item(connection, "revoked", status=ItemState.REVOKED)
    frozen = add_account(connection, "frozen", item_id=healthy)
    add_observation(Store(connection), "cycle", frozen, 100, source_as_of=NOW - timedelta(days=15))
    add_account(connection, "pending", item_id=healthy, reconciliation="NEW", included=False)
    manual = add_account(
        connection, "manual", item_id=None, policy=FreshnessPolicy.MANUAL_QTY_LIVE_PRICE
    )
    connection.execute(
        "INSERT INTO manual_asset(account_id, kind, symbol, share_count, valued_as_of) "
        "VALUES (?, 'EQUITY_SHARES', 'SYNTH', '2', ?)",
        (manual, (NOW - timedelta(days=100)).isoformat()),
    )
    add_observation(Store(connection), "cycle", manual, 200, source=ObservationSource.QUOTE)
    connection.commit()
    yield connection
    connection.close()


def runner(db: sqlite3.Connection, tmp_path: Path) -> AlertDispatcher:
    return AlertDispatcher(db, lock_path=tmp_path / "sync.lock", clock=lambda: NOW)


def test_all_five_kinds_reach_real_encrypted_publication(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    result = runner(db, tmp_path).run()
    assert {alert.kind for alert in result.raised} == set(AlertKind)
    assert not db.in_transaction
    # The account excluded from the headline still needs reconciliation.
    pending = next(a for a in result.raised if a.kind is AlertKind.PENDING_RECONCILIATION)
    assert pending.account_id == 2
    frozen = next(a for a in result.raised if a.kind is AlertKind.FROZEN_DATA)
    assert frozen.raised_source_as_of == NOW - timedelta(days=15)
    assert db.execute("SELECT notified_at FROM alert").fetchall() == [(None,)] * 5
    db.execute("BEGIN IMMEDIATE")
    Snapshotter(Store(db)).run("cycle", at=NOW)
    pairing = "00000000-0000-4000-8000-000000000016"
    db.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) VALUES (?, ?, 'synthetic', 'ACTIVE')",
        (pairing, NOW.isoformat()),
    )
    db.commit()
    key = bytes(range(32))
    publication = Publisher(db, lambda _: key).publish(at=NOW)
    payload = json.loads(open_payload(publication.envelope, key))
    assert {alert["kind"] for alert in payload["alerts"]} == {kind.value for kind in AlertKind}
    assert all(row[0] is not None for row in db.execute("SELECT notified_at FROM alert"))


def test_cached_source_ages_and_reopen_does_not_duplicate_alerts(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    dispatch = runner(db, tmp_path)
    first = dispatch.run()
    ids = {a.id for a in first.raised}
    assert len(ids) == 5
    assert dispatch.run().raised == ()
    with sqlite3.connect(tmp_path / "alerts.db") as reopened:
        migrate(reopened)
        other = runner(reopened, tmp_path)
        other._clock = lambda: NOW + timedelta(days=3)  # Sunday, no new provider observations.
        assert other.run().raised == ()
        assert {a.id for a in Store(reopened).alerts.open()} == ids
    reopened.close()
    assert db.execute("SELECT count(*) FROM sync_run").fetchone() == (1,)
    assert db.execute("SELECT count(*) FROM publication").fetchone() == (0,)


def test_wall_time_alone_can_raise_share_nudge(db: sqlite3.Connection, tmp_path: Path) -> None:
    db.execute(
        "UPDATE manual_asset SET valued_as_of = ?", ((NOW - timedelta(days=89)).isoformat(),)
    )
    db.commit()
    dispatch = runner(db, tmp_path)
    assert AlertKind.SHARE_COUNT_UNCONFIRMED not in {a.kind for a in dispatch.run().raised}
    dispatch._clock = lambda: NOW + timedelta(days=1)
    assert [a.kind for a in dispatch.run().raised] == [AlertKind.SHARE_COUNT_UNCONFIRMED]


def test_missing_price_preserves_freeze_but_read_absent_share_count_resolves_nudge(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    dispatch = runner(db, tmp_path)
    dispatch.run()
    freeze = next(a for a in Store(db).alerts.open() if a.kind is AlertKind.FROZEN_DATA)
    db.execute("DELETE FROM observation WHERE account_id = 1")
    db.execute("DELETE FROM manual_asset")
    db.commit()
    result = dispatch.run()
    assert [a.kind for a in result.resolved] == [AlertKind.SHARE_COUNT_UNCONFIRMED]
    assert freeze.id in {a.id for a in Store(db).alerts.open()}
    assert any(a.kind is AlertKind.PENDING_RECONCILIATION for a in Store(db).alerts.open())


def test_latest_observation_resolves_freeze_only_when_source_recovers(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    dispatch = runner(db, tmp_path)
    dispatch.run()
    add_run(db, "retry")
    add_observation(
        Store(db),
        "retry",
        1,
        100,
        source_as_of=NOW - timedelta(days=15),
        observed_at=NOW + timedelta(seconds=1),
    )
    db.commit()
    dispatch._clock = lambda: NOW + timedelta(seconds=1)
    assert dispatch.run().resolved == ()  # Fresh fetch, same old source.
    add_run(db, "fresh")
    add_observation(
        Store(db),
        "fresh",
        1,
        100,
        source_as_of=NOW,
        observed_at=NOW + timedelta(seconds=2),
    )
    db.commit()
    dispatch._clock = lambda: NOW + timedelta(seconds=2)
    assert [a.kind for a in dispatch.run().resolved] == [AlertKind.FROZEN_DATA]


@pytest.mark.parametrize("column", ["reconciliation_state", "archived_at", "superseded_at"])
def test_archived_subject_is_omitted_without_resolving_its_alert(
    db: sqlite3.Connection, tmp_path: Path, column: str
) -> None:
    dispatch = runner(db, tmp_path)
    dispatch.run()
    value = "ARCHIVED" if column == "reconciliation_state" else NOW.isoformat()
    db.execute(f"UPDATE account SET {column} = ? WHERE id = 2", (value,))
    db.commit()
    assert dispatch.run().resolved == ()
    assert any(a.kind is AlertKind.PENDING_RECONCILIATION for a in Store(db).alerts.open())


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE manual_asset SET valued_as_of = 'synthetic-private-bad-clock'",
        "UPDATE manual_asset SET valued_as_of = '2026-01-16T22:00:00Z'",
        "UPDATE manual_asset SET valued_as_of = '2026-01-15T12:00:00'",
        "UPDATE item SET status_since = '2026-01-16T22:00:00Z'",
        "UPDATE item SET last_health_poll_at = '2026-01-16T22:00:00Z'",
        "UPDATE observation SET observed_at = '2026-01-16T22:00:00Z'",
        "UPDATE observation SET fetched_at = '2026-01-16T22:00:00Z'",
        "UPDATE observation SET source_as_of = '2026-01-16T22:00:00Z'",
    ],
)
def test_bad_clocks_refuse_before_any_alert_changes(
    db: sqlite3.Connection, tmp_path: Path, change: str
) -> None:
    db.execute(change)
    db.commit()
    with pytest.raises(ValueError) as error:
        runner(db, tmp_path).run()
    assert "synthetic-private-bad-clock" not in str(error.value)
    assert db.execute("SELECT count(*) FROM alert").fetchone() == (0,)
    assert not db.in_transaction


def test_property_uses_effective_revision_not_latest_insertion(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    account = add_account(db, "property", item_id=None, policy=FreshnessPolicy.MANUAL_STATIC)
    for run_id, when in (
        ("effective", NOW - timedelta(days=10)),
        ("future", NOW + timedelta(days=1)),
    ):
        add_run(db, run_id)
        add_observation(
            Store(db),
            run_id,
            account,
            100,
            source_as_of=when,
            source=ObservationSource.MANUAL,
            source_clock=MANUAL_VALUED_AS_OF,
        )
    db.commit()
    assert len(runner(db, tmp_path).run().raised) == 5


def test_caller_transaction_and_canonical_lock_are_required(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="caller-owned"):
        CycleAlertEvaluator(db).evaluate(at=NOW)
    dispatch = runner(db, tmp_path)
    db.execute("BEGIN")
    with pytest.raises(ValueError, match="no active transaction"):
        dispatch.run()
    assert db.in_transaction
    db.rollback()
    with (
        exclusive_file_lock(tmp_path / "sync.lock", blocking=False, reentrant=False),
        pytest.raises(LockUnavailable),
    ):
        dispatch.run()
    assert db.execute("SELECT count(*) FROM alert").fetchone() == (0,)


def test_evaluation_and_writes_share_one_transaction_and_retry_rereads_state(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch = runner(db, tmp_path)
    evaluate = dispatch._evaluator.evaluate
    rival = sqlite3.connect(tmp_path / "alerts.db", timeout=0)
    migrate(rival)
    rival.execute("PRAGMA busy_timeout = 0")
    calls = 0
    waits = []

    def checked(*, at: datetime) -> AlertEvaluation:
        nonlocal calls
        calls += 1
        # Reservation must precede the first read, not just the first alert write.
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            rival.execute("BEGIN IMMEDIATE")
        result = evaluate(at=at)
        assert rival.execute("SELECT count(*) FROM alert").fetchone() == (0,)
        if calls == 1:
            error = sqlite3.OperationalError("synthetic busy")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise error
        return result

    def after_rollback(delay: float) -> None:
        waits.append(delay)
        assert not db.in_transaction
        assert db.execute("SELECT count(*) FROM alert").fetchone() == (0,)
        rival.execute("UPDATE account SET reconciliation_state = 'CONFIRMED' WHERE id = 2")
        rival.commit()

    monkeypatch.setattr(dispatch._evaluator, "evaluate", checked)
    monkeypatch.setattr(dispatch, "_sleep", after_rollback)
    try:
        result = dispatch.run()
        assert calls == 2 and len(waits) == 1
        assert len(result.raised) == 4
        assert AlertKind.PENDING_RECONCILIATION not in {a.kind for a in result.raised}
        assert rival.execute("SELECT count(*) FROM alert").fetchone() == (4,)
    finally:
        rival.close()


def test_partial_nonbusy_write_failure_rolls_back_all_alerts(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    db.execute(
        "CREATE TEMP TRIGGER reject_account_alert BEFORE INSERT ON alert "
        "WHEN NEW.account_id IS NOT NULL "
        "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
        runner(db, tmp_path).run()
    assert db.execute("SELECT count(*) FROM alert").fetchone() == (0,)
    db.execute("DROP TRIGGER reject_account_alert")
    assert len(runner(db, tmp_path).run().raised) == 5


def test_caller_can_rollback_evaluation_without_hidden_commit(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    rival = sqlite3.connect(tmp_path / "alerts.db")
    try:
        db.execute("BEGIN IMMEDIATE")
        result = CycleAlertEvaluator(db).evaluate(at=NOW)
        assert len(result.raised) == 5
        assert db.in_transaction
        assert rival.execute("SELECT count(*) FROM alert").fetchone() == (0,)
        db.rollback()
        assert db.execute("SELECT count(*) FROM alert").fetchone() == (0,)
    finally:
        rival.close()


def test_positive_item_reconciliation_and_confirmation_changes_resolve(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    dispatch = runner(db, tmp_path)
    dispatch.run()
    db.execute(
        "UPDATE item SET status = 'HEALTHY', status_since = ? WHERE id IN (2, 3)",
        (NOW.isoformat().replace("+00:00", "Z"),),
    )
    db.execute("UPDATE account SET reconciliation_state = 'CONFIRMED' WHERE id = 2")
    db.execute("UPDATE manual_asset SET valued_as_of = ?", (NOW.isoformat(),))
    db.commit()
    assert {a.kind for a in dispatch.run().resolved} == {
        AlertKind.NEEDS_REAUTH,
        AlertKind.REVOKED,
        AlertKind.PENDING_RECONCILIATION,
        AlertKind.SHARE_COUNT_UNCONFIRMED,
    }
    assert [a.kind for a in Store(db).alerts.open()] == [AlertKind.FROZEN_DATA]
