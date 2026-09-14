"""Task 17: the presentation layer can only perform honest, immutable reads."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from networth.manual import revision_draft
from networth.model import (
    DisplayState,
    FreshnessPolicy,
    FreshnessState,
    ItemState,
    ObservationDraft,
    ObservationSource,
    PropertyValuation,
    ReconciliationState,
    SnapshotAgeState,
    SourcedFigure,
)
from networth.query import AccountRead, NetWorthQuery, NetWorthQueryError, NetWorthRead
from networth.snapshotter import Snapshotter
from networth.storage import migrate
from networth.store import Store

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
SOURCE_AS_OF = NOW - timedelta(hours=1)


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


def add_run(connection: sqlite3.Connection, run_id: str, *, at: datetime = NOW) -> None:
    connection.execute(
        """
        INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok, error_summary)
        VALUES (?, ?, ?, 'TEST', 1, NULL)
        """,
        (
            run_id,
            (at - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            at.isoformat().replace("+00:00", "Z"),
        ),
    )


def add_item(
    connection: sqlite3.Connection,
    suffix: str,
    *,
    state: ItemState = ItemState.HEALTHY,
) -> int:
    institution = connection.execute(
        """
        INSERT INTO institution(plaid_institution_id, name, is_oauth)
        VALUES (?, ?, 0)
        """,
        (f"synthetic-institution-{suffix}", f"Synthetic institution {suffix}"),
    )
    assert institution.lastrowid is not None
    item = connection.execute(
        """
        INSERT INTO item(
            institution_id, plaid_item_id, secret_ref, status, status_since, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(institution.lastrowid),
            f"synthetic-item-{suffix}",
            f"SYNTHETIC_TOKEN_{suffix.upper()}",
            state.value,
            (NOW - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
            (NOW - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
        ),
    )
    assert item.lastrowid is not None
    return int(item.lastrowid)


def add_account(
    connection: sqlite3.Connection,
    suffix: str,
    *,
    item_id: int | None,
    policy: FreshnessPolicy,
    reconciliation: ReconciliationState = ReconciliationState.CONFIRMED,
    lineage_id: int | None = None,
) -> int:
    account = connection.execute(
        """
        INSERT INTO account(
            item_id, plaid_account_id, name, type, currency, sign,
            freshness_policy, include_in_net_worth, lineage_id,
            reconciliation_state, created_at
        ) VALUES (?, ?, ?, 'synthetic', 'USD', 1, ?, 1, ?, ?, ?)
        """,
        (
            item_id,
            None if item_id is None else f"synthetic-account-{suffix}",
            f"Synthetic account {suffix}",
            policy.value,
            lineage_id,
            reconciliation.value,
            (NOW - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
        ),
    )
    assert account.lastrowid is not None
    return int(account.lastrowid)


def add_observation(
    store: Store,
    run_id: str,
    account_id: int,
    value_minor: int,
    *,
    observed_at: datetime = NOW,
    source_as_of: datetime | None = SOURCE_AS_OF,
    source_clock: str = "SYNTHETIC_BALANCE_CLOCK",
) -> None:
    store.observations.append(
        ObservationDraft(
            sync_run_id=run_id,
            account_id=account_id,
            observed_at=observed_at,
            figure=SourcedFigure(value_minor, "USD", source_as_of, source_clock),
            source=ObservationSource.PLAID_BALANCE,
            fetched_at=observed_at,
            is_carried_forward=False,
        )
    )


def confirmed_read(db: sqlite3.Connection, store: Store, suffix: str) -> NetWorthRead:
    item_id = add_item(db, suffix)
    account_id = add_account(
        db,
        suffix,
        item_id=item_id,
        policy=FreshnessPolicy.SYNCED_BALANCE,
    )
    run_id = f"run-{suffix}"
    add_run(db, run_id)
    add_observation(store, run_id, account_id, 12_345)
    Snapshotter(store).run(run_id, at=NOW)
    result = NetWorthQuery(store).latest()
    assert result is not None
    return result


def test_latest_keeps_the_total_age_and_per_account_staleness_together(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    item_id = add_item(db, "latest")
    account_id = add_account(
        db,
        "latest",
        item_id=item_id,
        policy=FreshnessPolicy.SYNCED_BALANCE,
    )
    add_run(db, "run-latest")
    add_observation(store, "run-latest", account_id, 12_345)
    snapshot = Snapshotter(store).run("run-latest", at=NOW)

    result = NetWorthQuery(store).latest()

    assert result is not None
    assert result.snapshot == snapshot
    assert result.snapshot.net_worth == SourcedFigure(
        12_345,
        "USD",
        SOURCE_AS_OF,
        "OLDEST_CONTRIBUTING_SOURCE_AS_OF",
    )
    assert result.snapshot.age.state is SnapshotAgeState.KNOWN
    assert result.snapshot.counts.account_count == 1
    assert len(result.accounts) == 1
    assert result.accounts[0].observation is not None
    assert result.accounts[0].observation.figure.as_of == SOURCE_AS_OF
    assert result.accounts[0].freshness is not None
    assert result.accounts[0].freshness.state is FreshnessState.FRESH
    assert result.accounts[0].item_state is ItemState.HEALTHY
    assert result.display_state is DisplayState.OK


def test_latest_never_turns_an_unknown_source_clock_into_a_date(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    item_id = add_item(db, "unknown")
    account_id = add_account(
        db,
        "unknown",
        item_id=item_id,
        policy=FreshnessPolicy.SYNCED_BALANCE,
    )
    add_run(db, "run-unknown")
    add_observation(
        store,
        "run-unknown",
        account_id,
        23_456,
        source_as_of=None,
        source_clock="UNKNOWN",
    )
    Snapshotter(store).run("run-unknown", at=NOW)

    result = NetWorthQuery(store).latest()

    assert result is not None
    assert result.snapshot.age.state is SnapshotAgeState.UNKNOWN
    assert result.snapshot.net_worth.as_of is None
    assert result.accounts[0].freshness is not None
    assert result.accounts[0].freshness.state is FreshnessState.UNKNOWN
    assert result.accounts[0].freshness.source_as_of is None
    assert result.display_state is DisplayState.WAITING


def test_unreconciled_account_has_no_invented_value_and_requires_action(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    item_id = add_item(db, "new")
    add_account(
        db,
        "new",
        item_id=item_id,
        policy=FreshnessPolicy.SYNCED_BALANCE,
        reconciliation=ReconciliationState.NEW,
    )
    add_run(db, "run-new")
    snapshot = Snapshotter(store).run("run-new", at=NOW)

    result = NetWorthQuery(store).latest()

    assert result is not None
    assert snapshot.net_worth.value_minor == 0
    assert result.accounts[0].observation is None
    assert result.accounts[0].freshness is None
    assert result.accounts[0].item_state is ItemState.HEALTHY
    assert result.display_state is DisplayState.ACTION_NEEDED


def test_account_history_joins_every_account_id_in_the_lineage(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    original_item = add_item(db, "original")
    replacement_item = add_item(db, "replacement")
    original = add_account(
        db,
        "original",
        item_id=original_item,
        policy=FreshnessPolicy.SYNCED_BALANCE,
    )
    replacement = add_account(
        db,
        "replacement",
        item_id=replacement_item,
        policy=FreshnessPolicy.SYNCED_BALANCE,
        lineage_id=original,
    )
    add_run(db, "run-before")
    add_observation(store, "run-before", original, 10_000, observed_at=NOW - timedelta(days=1))
    add_run(db, "run-after", at=NOW + timedelta(days=1))
    add_observation(
        store,
        "run-after",
        replacement,
        11_000,
        observed_at=NOW + timedelta(days=1),
    )
    query = NetWorthQuery(store)

    original_history = query.account_history(original)
    replacement_history = query.account_history(replacement)

    assert [point.account_id for point in original_history] == [original, replacement]
    assert replacement_history == original_history


def test_total_history_is_chronological_and_reads_do_not_mutate_sqlite(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    item_id = add_item(db, "history")
    account_id = add_account(
        db,
        "history",
        item_id=item_id,
        policy=FreshnessPolicy.SYNCED_BALANCE,
    )
    add_run(db, "run-one", at=NOW)
    add_observation(store, "run-one", account_id, 10_000, observed_at=NOW)
    first = Snapshotter(store).run("run-one", at=NOW)
    later = NOW + timedelta(days=1)
    add_run(db, "run-two", at=later)
    add_observation(
        store,
        "run-two",
        account_id,
        12_000,
        observed_at=later,
        source_as_of=later - timedelta(hours=1),
    )
    second = Snapshotter(store).run("run-two", at=later)
    query = NetWorthQuery(store)
    changes_before_reads = db.total_changes

    assert query.history() == (first, second)
    assert query.latest() is not None
    assert query.account_history(account_id)[-1].figure.value_minor == 12_000
    assert db.total_changes == changes_before_reads


def test_latest_refuses_to_pair_a_stale_snapshot_with_a_new_account_population(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    item_id = add_item(db, "population-one")
    first_account = add_account(
        db,
        "population-one",
        item_id=item_id,
        policy=FreshnessPolicy.SYNCED_BALANCE,
    )
    add_run(db, "run-population")
    add_observation(store, "run-population", first_account, 10_000)
    Snapshotter(store).run("run-population", at=NOW)
    second_item = add_item(db, "population-two")
    add_account(
        db,
        "population-two",
        item_id=second_item,
        policy=FreshnessPolicy.SYNCED_BALANCE,
    )

    with pytest.raises(NetWorthQueryError, match="population.*new snapshot"):
        NetWorthQuery(store).latest()


def test_latest_refuses_an_equal_size_relink_population_change(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    old_item = add_item(db, "relink-old")
    old_account = add_account(
        db,
        "relink-old",
        item_id=old_item,
        policy=FreshnessPolicy.SYNCED_BALANCE,
    )
    add_run(db, "run-before-relink")
    add_observation(store, "run-before-relink", old_account, 10_000)
    Snapshotter(store).run("run-before-relink", at=NOW)

    replacement_item = add_item(db, "relink-new")
    replacement = add_account(
        db,
        "relink-new",
        item_id=replacement_item,
        policy=FreshnessPolicy.SYNCED_BALANCE,
        reconciliation=ReconciliationState.NEW,
        lineage_id=old_account,
    )
    db.execute(
        """
        UPDATE account
        SET superseded_by_account_id = ?, superseded_at = ?
        WHERE id = ?
        """,
        (replacement, NOW.isoformat().replace("+00:00", "Z"), old_account),
    )

    assert [account.id for account in store.accounts.for_snapshot()] == [replacement]
    with pytest.raises(NetWorthQueryError, match="population.*new snapshot"):
        NetWorthQuery(store).latest()


def test_latest_refuses_a_just_reconciled_account_until_the_next_snapshot(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    item_id = add_item(db, "reconciled")
    account_id = add_account(
        db,
        "reconciled",
        item_id=item_id,
        policy=FreshnessPolicy.SYNCED_BALANCE,
        reconciliation=ReconciliationState.NEW,
    )
    add_run(db, "run-before-reconciliation")
    Snapshotter(store).run("run-before-reconciliation", at=NOW)
    db.execute(
        "UPDATE account SET reconciliation_state = 'CONFIRMED' WHERE id = ?",
        (account_id,),
    )

    with pytest.raises(NetWorthQueryError, match="population.*new snapshot"):
        NetWorthQuery(store).latest()


def test_late_backdated_manual_revision_does_not_redraw_the_latest_snapshot(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    account_id = add_account(
        db,
        "property",
        item_id=None,
        policy=FreshnessPolicy.MANUAL_STATIC,
    )
    valued_as_of = NOW - timedelta(days=30)
    add_run(db, "run-property")
    original = store.observations.append(
        revision_draft(
            sync_run_id="run-property",
            account_id=account_id,
            valuation=PropertyValuation(30_000, "USD", valued_as_of),
            observed_at=NOW - timedelta(days=1),
        )
    )
    Snapshotter(store).run("run-property", at=NOW)

    entered_later = NOW + timedelta(hours=1)
    add_run(db, "run-late-correction", at=entered_later)
    store.observations.append(
        revision_draft(
            sync_run_id="run-late-correction",
            account_id=account_id,
            valuation=PropertyValuation(35_000, "USD", valued_as_of),
            observed_at=entered_later,
        )
    )

    result = NetWorthQuery(store).latest()

    assert result is not None
    assert result.snapshot.net_worth.value_minor == 30_000
    assert result.accounts[0].observation == original


def test_future_dated_manual_revision_is_not_used_before_it_takes_effect(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    account_id = add_account(
        db,
        "future-property",
        item_id=None,
        policy=FreshnessPolicy.MANUAL_STATIC,
    )
    add_run(db, "run-current-property", at=NOW - timedelta(days=2))
    current = store.observations.append(
        revision_draft(
            sync_run_id="run-current-property",
            account_id=account_id,
            valuation=PropertyValuation(30_000, "USD", NOW - timedelta(days=30)),
            observed_at=NOW - timedelta(days=2),
        )
    )
    add_run(db, "run-future-property", at=NOW - timedelta(days=1))
    store.observations.append(
        revision_draft(
            sync_run_id="run-future-property",
            account_id=account_id,
            valuation=PropertyValuation(99_000, "USD", NOW + timedelta(days=7)),
            observed_at=NOW - timedelta(days=1),
        )
    )
    add_run(db, "run-property-snapshot")
    Snapshotter(store).run("run-property-snapshot", at=NOW)

    result = NetWorthQuery(store).latest()

    assert result is not None
    assert result.snapshot.net_worth.value_minor == 30_000
    assert result.accounts[0].observation == current


def test_freshness_is_evaluated_at_the_snapshot_instant(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    item_id = add_item(db, "evaluation-instant")
    account_id = add_account(
        db,
        "evaluation-instant",
        item_id=item_id,
        policy=FreshnessPolicy.SYNCED_BALANCE,
    )
    add_run(db, "run-evaluation-instant")
    add_observation(
        store,
        "run-evaluation-instant",
        account_id,
        12_345,
        observed_at=NOW - timedelta(hours=2),
        source_as_of=NOW - timedelta(hours=37),
    )
    Snapshotter(store).run("run-evaluation-instant", at=NOW)

    result = NetWorthQuery(store).latest()

    assert result is not None
    assert result.accounts[0].freshness is not None
    assert result.accounts[0].freshness.state is FreshnessState.STALE
    assert result.display_state is DisplayState.WAITING


def test_account_read_rejects_wrong_field_types(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    base = confirmed_read(db, store, "account-read-types").accounts[0]
    assert base.observation is not None
    assert base.freshness is not None

    with pytest.raises(TypeError, match="account must be"):
        AccountRead(cast(Any, object()), base.observation, base.freshness, base.item_state)
    with pytest.raises(TypeError, match="observation must be"):
        AccountRead(base.account, cast(Any, object()), base.freshness, base.item_state)
    with pytest.raises(TypeError, match="freshness must be"):
        AccountRead(base.account, base.observation, cast(Any, object()), base.item_state)
    with pytest.raises(TypeError, match="item_state must be"):
        AccountRead(base.account, base.observation, base.freshness, cast(Any, object()))


def test_account_read_pins_contribution_and_item_pairings(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    base = confirmed_read(db, store, "account-read-pairings").accounts[0]
    assert base.observation is not None
    assert base.freshness is not None

    with pytest.raises(ValueError, match="confirmed account requires"):
        AccountRead(base.account, None, None, base.item_state)
    new_account = replace(base.account, reconciliation_state=ReconciliationState.NEW)
    with pytest.raises(ValueError, match="unreconciled account carries neither"):
        AccountRead(new_account, base.observation, base.freshness, base.item_state)
    with pytest.raises(ValueError, match="item_state is present exactly"):
        AccountRead(base.account, base.observation, base.freshness, None)


def test_account_read_pins_freshness_clock_and_item_state(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    base = confirmed_read(db, store, "account-read-freshness").accounts[0]
    assert base.observation is not None
    assert base.freshness is not None

    wrong_clock = replace(base.freshness, source_as_of=SOURCE_AS_OF - timedelta(minutes=1))
    with pytest.raises(ValueError, match="source clock"):
        AccountRead(base.account, base.observation, wrong_clock, base.item_state)
    wrong_item_state = replace(base.freshness, item_state=ItemState.DEGRADED)
    with pytest.raises(ValueError, match="same Item state"):
        AccountRead(base.account, base.observation, wrong_item_state, base.item_state)


def test_net_worth_read_pins_field_types_and_account_count(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    result = confirmed_read(db, store, "net-worth-read-invariants")

    with pytest.raises(TypeError, match="snapshot must be"):
        NetWorthRead(cast(Any, object()), result.accounts, result.display_state)
    with pytest.raises(TypeError, match="accounts must be"):
        NetWorthRead(result.snapshot, cast(Any, list(result.accounts)), result.display_state)
    with pytest.raises(TypeError, match="accounts must be"):
        NetWorthRead(result.snapshot, cast(Any, (object(),)), result.display_state)
    with pytest.raises(TypeError, match="display_state must be"):
        NetWorthRead(result.snapshot, result.accounts, cast(Any, object()))
    with pytest.raises(ValueError, match="account_count"):
        NetWorthRead(result.snapshot, (), result.display_state)


@pytest.mark.parametrize("account_id", [True, 0, -1])
def test_account_history_rejects_non_positive_integer_ids(
    store: Store,
    account_id: int,
) -> None:
    expected = TypeError if isinstance(account_id, bool) else ValueError
    with pytest.raises(expected):
        NetWorthQuery(store).account_history(account_id)
