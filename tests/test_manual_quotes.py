"""Manual cycle inputs, using synthetic holdings and real migrated WAL storage."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Sequence
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from networth.manual_quotes import ManualQuoteInputError, ManualQuoteWorker
from networth.model import FreshnessPolicy, ObservationSource, Quote
from networth.snapshotter import Snapshotter
from networth.storage import migrate
from networth.store import ObservationConflictError, Store
from tests.test_snapshotter import NOW, add_account, add_run


class Quotes:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.before: Callable[[], None] = lambda: None
        self.result = {"SYNTH": Quote("SYNTH", Decimal("12.50"), "USD", NOW - timedelta(hours=1))}

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        self.calls.append(tuple(symbols))
        self.before()
        return self.result


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(tmp_path / "quotes.db")
    migrate(connection)
    add_run(connection, "cycle")
    for suffix in ("one", "two"):
        account = add_account(
            connection, suffix, item_id=None, policy=FreshnessPolicy.MANUAL_QTY_LIVE_PRICE
        )
        connection.execute(
            "INSERT INTO manual_asset(account_id, kind, symbol, share_count, valued_as_of) "
            "VALUES (?, 'EQUITY_SHARES', 'synth', '2.5', ?)",
            (account, (NOW - timedelta(days=100)).isoformat()),
        )
    connection.commit()
    yield connection
    connection.close()


def worker(db: sqlite3.Connection, quotes: Quotes) -> ManualQuoteWorker:
    return ManualQuoteWorker(db, quotes, clock=lambda: NOW + timedelta(seconds=1))


def test_quote_collection_frees_writer_then_values_every_account_before_snapshot(
    db: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    quotes = Quotes()
    rival = sqlite3.connect(tmp_path / "quotes.db", timeout=0)

    def write() -> None:
        assert not db.in_transaction
        rival.execute("BEGIN IMMEDIATE")
        rival.execute("UPDATE account SET name = 'synthetic renamed'")
        rival.commit()

    quotes.before = write
    try:
        runner = worker(db, quotes)
        plan = runner.collect("cycle", at=NOW)
        assert quotes.calls == [("SYNTH",)]
        assert Store(db).observations.for_sync_run("cycle") == ()
        db.execute("BEGIN IMMEDIATE")
        assert runner.persist(plan, at=NOW + timedelta(seconds=1)) == 2
        snapshot = Snapshotter(Store(db)).run("cycle", at=NOW + timedelta(seconds=1))
        assert snapshot.net_worth.value_minor == 6250
        assert snapshot.age.as_of == NOW - timedelta(hours=1)
        assert rival.execute("SELECT count(*) FROM observation").fetchone() == (0,)
        db.commit()
        assert rival.execute("SELECT count(*) FROM observation").fetchone() == (2,)
        for observation in Store(db).observations.for_sync_run("cycle"):
            assert observation.source is ObservationSource.QUOTE
            assert observation.figure.source_clock == "QUOTE_AS_OF"
            assert observation.fetched_at == NOW + timedelta(seconds=1)
            assert not observation.is_carried_forward
        assert db.execute("SELECT DISTINCT last_source_as_of FROM account").fetchall() == [
            ("2026-01-15T21:00:00.000000Z",)
        ]
    finally:
        rival.close()


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE manual_asset SET share_count = '3' WHERE account_id = 1",
        "UPDATE manual_asset SET valued_as_of = '2026-01-15T00:00:00Z' WHERE account_id = 1",
        "UPDATE account SET include_in_net_worth = 0 WHERE id = 1",
        "UPDATE account SET reconciliation_state = 'NEW' WHERE id = 1",
        "DELETE FROM manual_asset WHERE account_id = 1",
    ],
)
def test_edit_during_fetch_refuses_whole_plan(
    db: sqlite3.Connection,
    tmp_path: Path,
    change: str,
) -> None:
    quotes = Quotes()

    def edit() -> None:
        with sqlite3.connect(tmp_path / "quotes.db") as rival:
            rival.execute(change)
        rival.close()

    quotes.before = edit
    runner = worker(db, quotes)
    plan = runner.collect("cycle", at=NOW)
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(ManualQuoteInputError):
        runner.persist(plan, at=NOW + timedelta(seconds=1))
    db.rollback()
    assert Store(db).observations.for_sync_run("cycle") == ()


def test_partial_persistence_rolls_back_and_retries_without_refetch(
    db: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    runner = worker(db, quotes := Quotes())
    plan = runner.collect("cycle", at=NOW)
    db.execute(
        "CREATE TEMP TRIGGER refuse_second BEFORE INSERT ON observation "
        "WHEN NEW.account_id = 2 BEGIN SELECT RAISE(ABORT, 'synthetic refusal'); END"
    )
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.IntegrityError):
        runner.persist(plan, at=NOW + timedelta(seconds=1))
    rival = sqlite3.connect(tmp_path / "quotes.db")
    try:
        assert rival.execute("SELECT count(*) FROM observation").fetchone() == (0,)
        db.rollback()
        db.execute("DROP TRIGGER refuse_second")
        db.execute("BEGIN IMMEDIATE")
        runner.persist(plan, at=NOW + timedelta(seconds=1))
        db.commit()
        assert quotes.calls == [("SYNTH",)]
        assert rival.execute("SELECT count(*) FROM observation").fetchone() == (2,)
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ObservationConflictError):
            runner.persist(plan, at=NOW + timedelta(seconds=1))
        db.rollback()
    finally:
        rival.close()


@pytest.mark.parametrize("bad", ["missing", "currency", "symbol", "future"])
def test_bad_quotes_never_become_observations(db: sqlite3.Connection, bad: str) -> None:
    quotes = Quotes()
    if bad == "missing":
        quotes.result = {}
    else:
        quotes.result = {
            "SYNTH": Quote(
                "WRONG" if bad == "symbol" else "SYNTH",
                Decimal("1"),
                "EUR" if bad == "currency" else "USD",
                NOW + timedelta(days=1) if bad == "future" else NOW,
            )
        }
    with pytest.raises(ManualQuoteInputError):
        worker(db, quotes).collect("cycle", at=NOW)
    assert not db.in_transaction
    assert Store(db).observations.for_sync_run("cycle") == ()


def test_transaction_guards_and_empty_population(db: sqlite3.Connection) -> None:
    runner = worker(db, quotes := Quotes())
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(ValueError, match="no active transaction"):
        runner.collect("cycle", at=NOW)
    db.rollback()
    assert quotes.calls == []
    plan = runner.collect("cycle", at=NOW)
    with pytest.raises(ValueError, match="caller-owned"):
        runner.persist(plan, at=NOW + timedelta(seconds=1))
    db.execute("UPDATE account SET include_in_net_worth = 0")
    db.commit()
    quotes.calls.clear()
    assert runner.collect("cycle", at=NOW).observations == ()
    assert quotes.calls == []


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE manual_asset SET share_count = 'NaN'",
        "UPDATE manual_asset SET valued_as_of = '2030-01-01T00:00:00Z'",
        "UPDATE manual_asset SET valued_as_of = '2020-01-01T00:00:00'",
        "UPDATE manual_asset SET symbol = ' '",
        "DELETE FROM manual_asset",
    ],
)
def test_bad_holdings_fail_before_network(db: sqlite3.Connection, statement: str) -> None:
    db.execute(statement)
    db.commit()
    quotes = Quotes()
    with pytest.raises(ManualQuoteInputError):
        worker(db, quotes).collect("cycle", at=NOW)
    assert quotes.calls == []
    assert not db.in_transaction


def test_quote_can_advance_during_fetch(db: sqlite3.Connection) -> None:
    quotes = Quotes()
    quote = Quote("SYNTH", Decimal("1"), "USD", NOW + timedelta(seconds=1))
    quotes.result = {"SYNTH": quote}
    runner = worker(db, quotes)
    plan = runner.collect("cycle", at=NOW)
    assert all(value.figure.as_of == quote.as_of for value in plan.observations)
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(ManualQuoteInputError, match="precedes collection"):
        runner.persist(plan, at=NOW)
    db.rollback()


@pytest.mark.parametrize(
    "selection",
    [
        "include_in_net_worth = 0",
        "reconciliation_state = 'ARCHIVED'",
        "archived_at = '2026-01-01T00:00:00Z'",
        "superseded_by_account_id = 2",
        "superseded_at = '2026-01-01T00:00:00Z'",
    ],
)
def test_inactive_account_is_not_quoted(db: sqlite3.Connection, selection: str) -> None:
    db.execute(f"UPDATE account SET {selection} WHERE id = 1")
    db.execute("UPDATE manual_asset SET symbol = 'IGNORED' WHERE account_id = 1")
    db.commit()
    quotes = Quotes()
    plan = worker(db, quotes).collect("cycle", at=NOW)
    assert quotes.calls == [("SYNTH",)]
    assert [value.account_id for value in plan.observations] == [2]


def test_account_and_holding_reads_share_one_snapshot(
    db: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    rival = sqlite3.connect(tmp_path / "quotes.db", timeout=0)
    changes: list[int] = []

    def after_account_read(statement: str) -> None:
        if statement.startswith("SELECT kind, symbol") and not changes:
            rival.execute("UPDATE manual_asset SET share_count = '10'")
            rival.commit()
            changes.append(1)

    db.set_trace_callback(after_account_read)
    try:
        runner = worker(db, Quotes())
        plan = runner.collect("cycle", at=NOW)
    finally:
        db.set_trace_callback(None)
        rival.close()
    assert changes == [1]
    assert [observation.figure.value_minor for observation in plan.observations] == [3125, 3125]
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(ManualQuoteInputError, match="inputs changed"):
        runner.persist(plan, at=NOW + timedelta(seconds=1))
    db.rollback()
