"""Task 04: append-only observation and snapshot repositories."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from networth.model import (
    AggregateSourceClock,
    Observation,
    ObservationDraft,
    ObservationSource,
    Snapshot,
    SnapshotAge,
    SnapshotAgeState,
    SnapshotCounts,
    SnapshotDraft,
    SourcedFigure,
)
from networth.storage import migrate
from networth.store import (
    ObservationConflictError,
    SnapshotConflictError,
    SnapshotRunNotSuccessfulError,
    Store,
)

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
SOURCE_AS_OF = datetime(2026, 1, 14, 21, 0, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def store(db: sqlite3.Connection) -> Store:
    return Store(db)


def add_sync_run(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    ok: bool | None = True,
) -> None:
    finished_at = None if ok is None else NOW.isoformat().replace("+00:00", "Z")
    connection.execute(
        """
        INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok, error_summary)
        VALUES (?, ?, ?, 'TEST', ?, ?)
        """,
        (
            run_id,
            (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            finished_at,
            None if ok is None else int(ok),
            "synthetic failure" if ok is False else None,
        ),
    )


def add_account(
    connection: sqlite3.Connection,
    name: str,
    *,
    lineage_id: int | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO account(
            name, type, currency, sign, freshness_policy,
            include_in_net_worth, lineage_id, reconciliation_state, created_at
        ) VALUES (?, 'synthetic', 'USD', 1, 'SYNCED_BALANCE', 1, ?, 'CONFIRMED', ?)
        """,
        (name, lineage_id, NOW.isoformat().replace("+00:00", "Z")),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def observation(
    run_id: str,
    account_id: int,
    value_minor: int,
    *,
    observed_at: datetime = NOW,
    as_of: datetime | None = SOURCE_AS_OF,
    source_clock: str = "SYNTHETIC_BALANCE_CLOCK",
    carried_forward: bool = False,
) -> ObservationDraft:
    return ObservationDraft(
        sync_run_id=run_id,
        account_id=account_id,
        observed_at=observed_at,
        figure=SourcedFigure(value_minor, "USD", as_of, source_clock),
        source=ObservationSource.PLAID_BALANCE,
        fetched_at=NOW,
        is_carried_forward=carried_forward,
    )


def snapshot(
    run_id: str,
    value_minor: int,
    *,
    taken_at: datetime = NOW,
    account_count: int = 1,
    age: SnapshotAge | None = None,
) -> SnapshotDraft:
    snapshot_age = age or SnapshotAge(
        SnapshotAgeState.KNOWN,
        SOURCE_AS_OF,
        SOURCE_AS_OF,
    )
    return SnapshotDraft(
        sync_run_id=run_id,
        taken_at=taken_at,
        net_worth=snapshot_age.figure(value_minor),
        assets=snapshot_age.figure(value_minor),
        liabilities=snapshot_age.figure(0),
        counts=SnapshotCounts(account_count, 0, 0, 0, 0, 0),
        is_complete=True,
        age=snapshot_age,
    )


def test_observation_round_trip_keeps_the_value_with_both_clocks(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_sync_run(db, "run-observation")
    account_id = add_account(db, "Synthetic account")
    draft = observation("run-observation", account_id, 12_345)

    stored = store.observations.append(draft)
    read = store.observations.get(stored.id)

    assert isinstance(read, Observation)
    assert read.figure == SourcedFigure(
        value_minor=12_345,
        currency="USD",
        as_of=SOURCE_AS_OF,
        source_clock="SYNTHETIC_BALANCE_CLOCK",
    )
    assert read.fetched_at == NOW
    assert read.fetched_at != read.figure.as_of


def test_unknown_source_clock_round_trips_without_becoming_fresh(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_sync_run(db, "run-unknown")
    account_id = add_account(db, "Synthetic undated account")

    stored = store.observations.append(
        observation(
            "run-unknown",
            account_id,
            23_456,
            as_of=None,
            source_clock="UNKNOWN",
        )
    )

    assert stored.figure.as_of is None
    assert stored.figure.source_clock == "UNKNOWN"
    assert store.observations.latest_for_account(account_id) == stored


def test_correction_is_a_new_row_never_an_in_place_edit(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    account_id = add_account(db, "Synthetic corrected account")
    add_sync_run(db, "run-before-correction")
    original = store.observations.append(observation("run-before-correction", account_id, 11_111))

    with pytest.raises(ObservationConflictError, match="correction in a new run"):
        store.observations.append(observation("run-before-correction", account_id, 22_222))

    assert store.observations.get(original.id) == original
    add_sync_run(db, "run-correction")
    correction = store.observations.append(
        observation(
            "run-correction",
            account_id,
            22_222,
            observed_at=NOW + timedelta(days=1),
        )
    )

    history = store.observations.history_for_lineage(account_id)
    assert history == (original, correction)
    assert db.execute("SELECT count(*) FROM observation").fetchone() == (2,)


@pytest.mark.parametrize("ok", [None, False])
def test_snapshot_requires_a_finished_successful_run(
    db: sqlite3.Connection,
    store: Store,
    ok: bool | None,
) -> None:
    add_sync_run(db, "run-not-successful", ok=ok)

    with pytest.raises(SnapshotRunNotSuccessfulError, match="finished successful"):
        store.snapshots.append(snapshot("run-not-successful", 12_345))

    assert db.execute("SELECT count(*) FROM snapshot").fetchone() == (0,)


def test_snapshot_requires_an_existing_run(db: sqlite3.Connection, store: Store) -> None:
    with pytest.raises(SnapshotRunNotSuccessfulError, match="finished successful"):
        store.snapshots.append(snapshot("missing-run", 12_345))


def test_snapshot_retry_is_idempotent_on_sync_run_id(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_sync_run(db, "run-idempotent")
    draft = snapshot("run-idempotent", 34_567)

    first = store.snapshots.append(draft)
    retried = store.snapshots.append(draft)

    assert retried == first
    assert retried.id == first.id
    assert db.execute("SELECT count(*) FROM snapshot").fetchone() == (1,)


def test_snapshot_retry_with_different_data_refuses_to_hide_a_correction(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_sync_run(db, "run-conflict")
    original = snapshot("run-conflict", 34_567)
    stored = store.snapshots.append(original)
    conflicting = replace(
        original,
        net_worth=original.age.figure(45_678),
        assets=original.age.figure(45_678),
    )

    with pytest.raises(SnapshotConflictError, match="corrections require a new run"):
        store.snapshots.append(conflicting)

    assert store.snapshots.get(stored.id) == stored
    assert db.execute("SELECT count(*) FROM snapshot").fetchone() == (1,)


def test_queries_discover_an_account_added_mid_history(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    first_account = add_account(db, "Synthetic first account")
    add_sync_run(db, "run-one-account")
    first_observation = store.observations.append(
        observation("run-one-account", first_account, 10_000)
    )
    first_snapshot = store.snapshots.append(snapshot("run-one-account", 10_000, account_count=1))

    second_account = add_account(db, "Synthetic later account")
    add_sync_run(db, "run-two-accounts")
    next_first = store.observations.append(
        observation(
            "run-two-accounts",
            first_account,
            12_000,
            observed_at=NOW + timedelta(days=1),
        )
    )
    next_second = store.observations.append(
        observation(
            "run-two-accounts",
            second_account,
            8_000,
            observed_at=NOW + timedelta(days=1),
        )
    )
    second_snapshot = store.snapshots.append(
        snapshot(
            "run-two-accounts",
            20_000,
            taken_at=NOW + timedelta(days=1),
            account_count=2,
        )
    )

    assert store.observations.for_sync_run("run-one-account") == (first_observation,)
    assert store.observations.for_sync_run("run-two-accounts") == (
        next_first,
        next_second,
    )
    assert store.observations.latest_by_account() == (next_first, next_second)
    assert store.snapshots.history() == (first_snapshot, second_snapshot)
    assert [point.counts.account_count for point in store.snapshots.history()] == [1, 2]


def test_lineage_history_crosses_replacement_account_ids(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    original_account = add_account(db, "Synthetic original account")
    replacement_account = add_account(
        db,
        "Synthetic replacement account",
        lineage_id=original_account,
    )
    add_sync_run(db, "run-before-replacement")
    before = store.observations.append(
        observation("run-before-replacement", original_account, 10_000)
    )
    add_sync_run(db, "run-after-replacement")
    after = store.observations.append(
        observation(
            "run-after-replacement",
            replacement_account,
            11_000,
            observed_at=NOW + timedelta(days=1),
        )
    )

    assert store.observations.history_for_lineage(original_account) == (before, after)


@pytest.mark.parametrize(
    ("age", "source_clock"),
    [
        (
            SnapshotAge(SnapshotAgeState.KNOWN, SOURCE_AS_OF, SOURCE_AS_OF),
            AggregateSourceClock.OLDEST_CONTRIBUTING_SOURCE_AS_OF.value,
        ),
        (
            SnapshotAge(SnapshotAgeState.UNKNOWN, None, SOURCE_AS_OF),
            AggregateSourceClock.UNKNOWN.value,
        ),
        (
            SnapshotAge(SnapshotAgeState.STATIC_ONLY, None, None),
            AggregateSourceClock.STATIC_ONLY.value,
        ),
    ],
)
def test_every_snapshot_figure_read_includes_as_of_and_source_clock(
    db: sqlite3.Connection,
    store: Store,
    age: SnapshotAge,
    source_clock: str,
) -> None:
    run_id = f"run-{age.state.value.lower()}"
    add_sync_run(db, run_id)
    store.snapshots.append(snapshot(run_id, 56_789, age=age))
    read = store.snapshots.for_sync_run(run_id)

    assert isinstance(read, Snapshot)
    for figure in (read.net_worth, read.assets, read.liabilities):
        assert isinstance(figure, SourcedFigure)
        assert figure.as_of == age.as_of
        assert figure.source_clock == source_clock


def test_latest_uses_observation_time_not_insertion_order(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    account_id = add_account(db, "Synthetic out-of-order account")
    add_sync_run(db, "run-newer")
    newer = store.observations.append(
        observation(
            "run-newer",
            account_id,
            20_000,
            observed_at=NOW + timedelta(days=1),
        )
    )
    add_sync_run(db, "run-older")
    store.observations.append(observation("run-older", account_id, 10_000, observed_at=NOW))

    assert store.observations.latest_for_account(account_id) == newer


def test_store_leaves_transaction_commit_and_rollback_to_its_caller(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_sync_run(db, "run-rollback")
    account_id = add_account(db, "Synthetic rollback account")
    db.commit()

    db.execute("BEGIN IMMEDIATE")
    stored = store.observations.append(observation("run-rollback", account_id, 12_345))
    db.rollback()

    assert store.observations.get(stored.id) is None


def test_append_only_repositories_offer_no_mutation_or_number_only_api(store: Store) -> None:
    for repository in (store.observations, store.snapshots):
        assert not hasattr(repository, "update")
        assert not hasattr(repository, "delete")
        assert not hasattr(repository, "value")
        assert not hasattr(repository, "total")
