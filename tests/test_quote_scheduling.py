"""Source clocks, market boundaries and committed WAL views for quote due work."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from networth.model import FreshnessPolicy, ObservationSource, SnapshotAccount
from networth.quote_scheduling import QuoteRefreshSchedule, QuoteScheduleStateError
from networth.storage import migrate
from networth.store import AccountRepository, Store
from tests.test_snapshotter import add_account, add_observation, add_run

CLOSE = datetime(2026, 9, 18, 20, tzinfo=UTC)
NOW = CLOSE + timedelta(minutes=1)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(tmp_path / "quotes.db")
    migrate(connection)
    yield connection
    connection.close()


def account(db: sqlite3.Connection, name: str = "manual") -> int:
    return add_account(db, name, item_id=None, policy=FreshnessPolicy.MANUAL_QTY_LIVE_PRICE)


def price(
    db: sqlite3.Connection,
    account_id: int,
    source: datetime | None,
    *,
    run_id: str = "quote",
    at: datetime = NOW,
) -> None:
    add_run(db, run_id, at=at)
    add_observation(
        Store(db),
        run_id,
        account_id,
        1200,
        source_as_of=source,
        source_clock="UNKNOWN" if source is None else "QUOTE_AS_OF",
        source=ObservationSource.QUOTE,
        observed_at=at,
    )


def test_empty_and_missing_price_are_distinct_without_writes(db: sqlite3.Connection) -> None:
    planner = QuoteRefreshSchedule(db)
    assert not planner.due(at=NOW).due
    account(db)
    db.commit()
    before = db.total_changes
    status = planner.due(at=NOW)
    assert status.due and status.target_count == status.due_count == 1
    assert status.market_close == CLOSE and status.checked_at == NOW
    assert status == planner.due(at=NOW)
    assert db.total_changes == before and not db.in_transaction


@pytest.mark.parametrize(
    ("source", "due"),
    [(None, True), (CLOSE - timedelta(microseconds=1), True), (CLOSE, False), (NOW, False)],
)
def test_due_uses_price_age_not_recent_fetch_or_success(
    db: sqlite3.Connection, source: datetime | None, due: bool
) -> None:
    target = account(db)
    price(db, target, source)
    # A recent account summary is not proof about the observation's source.
    db.execute(
        "UPDATE account SET last_source_as_of = ?, last_fetch_at = ?",
        (NOW.isoformat().replace("+00:00", "Z"), NOW.isoformat().replace("+00:00", "Z")),
    )
    db.commit()
    assert QuoteRefreshSchedule(db).due(at=NOW).due is due


@pytest.mark.parametrize("kind", ["FULL_SYNC", "OTHER"])
def test_both_cycle_kinds_supply_prices_and_stale_refetch_stays_due(
    db: sqlite3.Connection, tmp_path: Path, kind: str
) -> None:
    target = account(db)
    price(db, target, CLOSE - timedelta(minutes=1), run_id="old")
    price(
        db, target, CLOSE - timedelta(minutes=1), run_id="refetched", at=NOW + timedelta(seconds=1)
    )
    db.execute("UPDATE sync_run SET kind = ?", (kind,))
    db.commit()
    reopened = sqlite3.connect(tmp_path / "quotes.db")
    migrate(reopened)
    try:
        assert QuoteRefreshSchedule(reopened).due(at=NOW + timedelta(seconds=1)).due
        price(db, target, CLOSE, run_id="fresh", at=NOW + timedelta(seconds=2))
        db.execute("UPDATE sync_run SET kind = ? WHERE id = 'fresh'", (kind,))
        db.commit()
        assert not QuoteRefreshSchedule(reopened).due(at=NOW + timedelta(seconds=2)).due
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "close",
    [CLOSE, datetime(2026, 11, 27, 18, tzinfo=UTC), datetime(2026, 11, 30, 21, tzinfo=UTC)],
)
def test_close_is_immediate_including_early_close_and_dst(
    db: sqlite3.Connection, close: datetime
) -> None:
    target = account(db)
    price(db, target, close - timedelta(minutes=1), at=close - timedelta(minutes=1))
    db.commit()
    planner = QuoteRefreshSchedule(db)
    assert not planner.due(at=close - timedelta(microseconds=1)).due
    assert planner.due(at=close).due
    assert planner.due(at=close).market_close == close


def test_weekend_holiday_and_twenty_hours_do_not_invent_a_new_close(db: sqlite3.Connection) -> None:
    # Friday before Labor Day; Monday's holiday must retain Friday's close.
    close = datetime(2026, 9, 4, 20, tzinfo=UTC)
    price(db, account(db), close, at=close)
    db.commit()
    status = QuoteRefreshSchedule(db).due(at=datetime(2026, 9, 7, 22, tzinfo=UTC))
    assert not status.due and status.market_close == close


@pytest.mark.parametrize(
    "change",
    [
        "include_in_net_worth = 0",
        "reconciliation_state = 'ARCHIVED'",
        "archived_at = '2026-09-18T19:00:00Z'",
        "superseded_by_account_id = 2",
        "superseded_at = '2026-09-18T19:00:00Z'",
    ],
)
def test_selection_matches_manual_worker(db: sqlite3.Connection, change: str) -> None:
    account(db)
    add_account(db, "property", item_id=None, policy=FreshnessPolicy.MANUAL_STATIC)
    db.execute(f"UPDATE account SET {change} WHERE id = 1")
    db.commit()
    status = QuoteRefreshSchedule(db).due(at=NOW)
    assert status.target_count == 0 and not status.due


def test_one_new_account_with_no_price_makes_the_batch_due(db: sqlite3.Connection) -> None:
    price(db, account(db), CLOSE)
    second = account(db, "second")
    db.execute("UPDATE account SET reconciliation_state = 'NEW' WHERE id = ?", (second,))
    db.commit()
    status = QuoteRefreshSchedule(db).due(at=NOW)
    assert status.due and status.target_count == 2 and status.due_count == 1


@pytest.mark.parametrize("column", ["observed_at", "fetched_at", "source_as_of"])
def test_future_evidence_refuses_instead_of_suppressing_work(
    db: sqlite3.Connection, column: str
) -> None:
    price(db, account(db), CLOSE)
    db.execute(
        f"UPDATE observation SET {column} = ?",
        ((NOW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),),
    )
    db.commit()
    with pytest.raises(QuoteScheduleStateError, match="invalid stored quote scheduling evidence"):
        QuoteRefreshSchedule(db).due(at=NOW)
    assert not db.in_transaction


def test_malformed_clock_is_sanitized_and_connection_released(db: sqlite3.Connection) -> None:
    price(db, account(db), CLOSE)
    db.execute("UPDATE observation SET source_as_of = 'synthetic-invalid-clock'")
    db.commit()
    with pytest.raises(QuoteScheduleStateError) as error:
        QuoteRefreshSchedule(db).due(at=NOW)
    assert str(error.value) == "invalid stored quote scheduling evidence"
    assert not db.in_transaction


def test_caller_transaction_is_preserved_and_non_utc_time_refused(db: sqlite3.Connection) -> None:
    planner = QuoteRefreshSchedule(db)
    with pytest.raises(ValueError, match="quote scheduling time"):
        planner.due(at=NOW.replace(tzinfo=None))
    db.execute("BEGIN")
    account(db)
    with pytest.raises(ValueError, match="no active transaction"):
        planner.due(at=NOW)
    assert db.in_transaction
    assert db.execute("SELECT count(*) FROM account").fetchone() == (1,)
    db.rollback()


def test_selection_and_source_clocks_share_one_committed_view(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = account(db)
    price(db, target, CLOSE - timedelta(seconds=1))
    db.commit()
    planner = QuoteRefreshSchedule(db)
    repository = type(planner._store.accounts)
    original = repository.for_snapshot
    rival = sqlite3.connect(tmp_path / "quotes.db", timeout=0)

    def change_after_selection(self: AccountRepository) -> tuple[SnapshotAccount, ...]:
        selected = original(self)
        rival.execute("BEGIN IMMEDIATE")
        rival.execute(
            "UPDATE observation SET source_as_of = ?", (CLOSE.isoformat().replace("+00:00", "Z"),)
        )
        rival.commit()
        return selected

    monkeypatch.setattr(repository, "for_snapshot", change_after_selection)
    try:
        assert planner.due(at=NOW).due
        monkeypatch.undo()
        assert not planner.due(at=NOW).due
    finally:
        rival.close()
