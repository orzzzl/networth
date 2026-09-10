"""Task 14: compute a total only as a fully annotated stored snapshot."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from networth.model import (
    MANUAL_VALUED_AS_OF,
    FreshnessPolicy,
    ItemState,
    ObservationDraft,
    ObservationSource,
    Snapshot,
    SnapshotAge,
    SnapshotAgeState,
    SnapshotCounts,
    SourcedFigure,
)
from networth.snapshotter import SnapshotInputError, Snapshotter
from networth.storage import migrate
from networth.store import Store

NOW = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)
NEWER_SOURCE = NOW - timedelta(hours=1)
OLDER_SOURCE = NOW - timedelta(hours=2)


def _db_time(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


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
        INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok)
        VALUES (?, ?, ?, 'TEST', 1)
        """,
        (run_id, _db_time(at - timedelta(minutes=1)), _db_time(at)),
    )


def add_item(
    connection: sqlite3.Connection,
    suffix: str,
    *,
    status: ItemState = ItemState.HEALTHY,
) -> int:
    institution = connection.execute(
        """
        INSERT INTO institution(plaid_institution_id, name, is_oauth)
        VALUES (?, ?, 0)
        """,
        (f"synthetic-provider-{suffix}", f"Synthetic provider {suffix}"),
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
            f"synthetic-ref-{suffix}",
            status.value,
            _db_time(NOW - timedelta(days=10)),
            _db_time(NOW - timedelta(days=20)),
        ),
    )
    assert item.lastrowid is not None
    return int(item.lastrowid)


def add_account(
    connection: sqlite3.Connection,
    suffix: str,
    *,
    item_id: int | None,
    policy: FreshnessPolicy = FreshnessPolicy.SYNCED_BALANCE,
    sign: int = 1,
    reconciliation: str = "CONFIRMED",
    included: bool = True,
    archived_at: datetime | None = None,
) -> int:
    linked_id = f"synthetic-account-{suffix}" if item_id is not None else None
    account = connection.execute(
        """
        INSERT INTO account(
            item_id, plaid_account_id, name, type, currency, sign,
            freshness_policy, include_in_net_worth, reconciliation_state,
            created_at, archived_at
        ) VALUES (?, ?, ?, 'synthetic', 'USD', ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            linked_id,
            f"Synthetic account {suffix}",
            sign,
            policy.value,
            int(included),
            reconciliation,
            _db_time(NOW - timedelta(days=20)),
            None if archived_at is None else _db_time(archived_at),
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
    source_as_of: datetime | None = NEWER_SOURCE,
    source_clock: str = "SYNTHETIC_SOURCE_CLOCK",
    source: ObservationSource = ObservationSource.PLAID_BALANCE,
    carried_forward: bool = False,
    observed_at: datetime = NOW,
    currency: str = "USD",
) -> None:
    store.observations.append(
        ObservationDraft(
            sync_run_id=run_id,
            account_id=account_id,
            observed_at=observed_at,
            figure=SourcedFigure(
                value_minor=value_minor,
                currency=currency,
                as_of=source_as_of,
                source_clock=source_clock,
            ),
            source=source,
            fetched_at=observed_at,
            is_carried_forward=carried_forward,
        )
    )


def test_snapshotter_returns_no_bare_total_and_uses_the_oldest_source_clock(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_run(db, "run-known")
    item_id = add_item(db, "known")
    asset = add_account(db, "asset", item_id=item_id)
    liability = add_account(db, "liability", item_id=item_id, sign=-1)
    add_observation(store, "run-known", asset, 10_000, source_as_of=NEWER_SOURCE)
    add_observation(store, "run-known", liability, 2_000, source_as_of=OLDER_SOURCE)

    result = Snapshotter(store).run("run-known", at=NOW)

    age = SnapshotAge(SnapshotAgeState.KNOWN, OLDER_SOURCE, OLDER_SOURCE)
    assert isinstance(result, Snapshot)
    assert not hasattr(result, "total_net_worth_minor")
    assert result.net_worth == age.figure(8_000)
    assert result.assets == age.figure(10_000)
    assert result.liabilities == age.figure(2_000)
    assert result.counts == SnapshotCounts(2, 0, 0, 0, 0, 0)
    assert result.is_complete is True
    assert store.snapshots.for_sync_run("run-known") == result


def test_stale_value_still_contributes_and_marks_the_snapshot_incomplete(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_run(db, "run-stale")
    item_id = add_item(db, "stale")
    account_id = add_account(db, "stale", item_id=item_id)
    stale_clock = NOW - timedelta(days=3)
    add_observation(
        store,
        "run-stale",
        account_id,
        12_345,
        source_as_of=stale_clock,
        carried_forward=True,
    )

    result = Snapshotter(store).run("run-stale", at=NOW)

    assert result.net_worth.value_minor == 12_345
    assert result.counts.stale_account_count == 1
    assert result.is_complete is False


def test_stale_without_carry_forward_still_marks_the_snapshot_incomplete(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_run(db, "run-stale-fetched")
    item_id = add_item(db, "stale-fetched")
    account_id = add_account(db, "stale-fetched", item_id=item_id)
    add_observation(
        store,
        "run-stale-fetched",
        account_id,
        12_345,
        source_as_of=NOW - timedelta(days=3),
        carried_forward=False,
    )

    result = Snapshotter(store).run("run-stale-fetched", at=NOW)

    assert result.counts.stale_account_count == 1
    assert result.is_complete is False


def test_a_frozen_source_clock_is_stale_and_incomplete_even_though_the_call_worked(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_run(db, "run-frozen")
    item_id = add_item(db, "frozen")
    account_id = add_account(
        db,
        "frozen",
        item_id=item_id,
        policy=FreshnessPolicy.SYNCED_HOLDINGS,
    )
    add_observation(
        store,
        "run-frozen",
        account_id,
        50_000,
        source_as_of=NOW - timedelta(days=30),
        source=ObservationSource.PLAID_HOLDINGS,
        carried_forward=False,
    )

    result = Snapshotter(store).run("run-frozen", at=NOW)

    assert result.counts.stale_account_count == 1
    assert result.is_complete is False
    assert result.age.state is SnapshotAgeState.KNOWN


def test_a_carried_forward_value_is_incomplete_even_while_its_source_clock_is_fresh(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_run(db, "run-carried-fresh")
    item_id = add_item(db, "carried-fresh")
    account_id = add_account(db, "carried-fresh", item_id=item_id)
    add_observation(
        store,
        "run-carried-fresh",
        account_id,
        12_345,
        carried_forward=True,
    )

    result = Snapshotter(store).run("run-carried-fresh", at=NOW)

    assert result.counts.stale_account_count == 0
    assert result.is_complete is False


def test_a_weekend_or_holiday_holding_clock_keeps_the_whole_headline_undated(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    """Task 12 deliberately maps a date with no market session to UNKNOWN.

    Task 14 chooses the conservative branch carried from PR #62: one such
    contributor poisons the headline date rather than borrowing the other
    account's usable date or inventing a prior/next market close.
    """

    add_run(db, "run-unknown")
    item_id = add_item(db, "unknown")
    known = add_account(db, "known-clock", item_id=item_id)
    non_session = add_account(
        db,
        "non-session-clock",
        item_id=item_id,
        policy=FreshnessPolicy.SYNCED_HOLDINGS,
    )
    add_observation(store, "run-unknown", known, 10_000, source_as_of=OLDER_SOURCE)
    add_observation(
        store,
        "run-unknown",
        non_session,
        20_000,
        source_as_of=None,
        source_clock="UNKNOWN",
        source=ObservationSource.PLAID_HOLDINGS,
    )

    result = Snapshotter(store).run("run-unknown", at=NOW)

    assert result.net_worth.value_minor == 30_000
    assert result.net_worth.as_of is None
    assert result.age == SnapshotAge(SnapshotAgeState.UNKNOWN, None, OLDER_SOURCE)
    assert result.counts.unknown_freshness_account_count == 1


def test_static_assets_are_outside_the_age_basis_and_use_the_revision_in_force(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    first_date = datetime(2023, 6, 1, tzinfo=UTC)
    future_date = datetime(2027, 6, 1, tzinfo=UTC)
    add_run(db, "run-first-revision", at=NOW - timedelta(days=20))
    add_run(db, "run-future-revision", at=NOW - timedelta(days=10))
    add_run(db, "run-static")
    account_id = add_account(
        db,
        "property",
        item_id=None,
        policy=FreshnessPolicy.MANUAL_STATIC,
    )
    add_observation(
        store,
        "run-first-revision",
        account_id,
        40_000_000,
        source_as_of=first_date,
        source_clock=MANUAL_VALUED_AS_OF,
        source=ObservationSource.MANUAL,
        observed_at=NOW - timedelta(days=20),
    )
    add_observation(
        store,
        "run-future-revision",
        account_id,
        55_000_000,
        source_as_of=future_date,
        source_clock=MANUAL_VALUED_AS_OF,
        source=ObservationSource.MANUAL,
        observed_at=NOW - timedelta(days=10),
    )

    result = Snapshotter(store).run("run-static", at=NOW)

    assert result.net_worth.value_minor == 40_000_000
    assert result.age == SnapshotAge(SnapshotAgeState.STATIC_ONLY, None, None)
    assert result.counts == SnapshotCounts(1, 0, 0, 1, 0, 0)
    assert result.is_complete is True


def test_unreconciled_account_is_counted_but_contributes_nothing(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_run(db, "run-new")
    item_id = add_item(db, "new")
    confirmed = add_account(db, "confirmed", item_id=item_id)
    pending = add_account(db, "pending", item_id=item_id, reconciliation="NEW")
    add_observation(store, "run-new", confirmed, 10_000)
    add_observation(store, "run-new", pending, 90_000)

    result = Snapshotter(store).run("run-new", at=NOW)

    assert result.net_worth.value_minor == 10_000
    assert result.counts.account_count == 2
    assert result.counts.unreconciled_account_count == 1
    assert result.counts.unknown_freshness_account_count == 0
    assert result.is_complete is False


def test_each_exclusion_or_archive_marker_keeps_an_account_out_of_the_snapshot(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_run(db, "run-membership")
    item_id = add_item(db, "membership")
    included = add_account(db, "included", item_id=item_id)
    add_observation(store, "run-membership", included, 10_000)
    add_account(db, "excluded", item_id=item_id, included=False)
    add_account(db, "state-archived", item_id=item_id, reconciliation="ARCHIVED")
    add_account(db, "time-archived", item_id=item_id, archived_at=NOW)
    superseded_by = add_account(db, "replacement", item_id=item_id, included=False)
    superseded = add_account(db, "superseded-by", item_id=item_id)
    superseded_at = add_account(db, "superseded-at", item_id=item_id)
    db.execute(
        "UPDATE account SET superseded_by_account_id = ? WHERE id = ?",
        (superseded_by, superseded),
    )
    db.execute(
        "UPDATE account SET superseded_at = ? WHERE id = ?",
        (_db_time(NOW), superseded_at),
    )

    result = Snapshotter(store).run("run-membership", at=NOW)

    assert result.counts.account_count == 1
    assert result.net_worth.value_minor == 10_000


def test_reauth_is_counted_but_does_not_alone_make_a_fresh_total_incomplete(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_run(db, "run-reauth")
    item_id = add_item(db, "reauth", status=ItemState.NEEDS_REAUTH)
    account_id = add_account(db, "reauth", item_id=item_id)
    add_observation(store, "run-reauth", account_id, 10_000)

    result = Snapshotter(store).run("run-reauth", at=NOW)

    assert result.counts.reauth_account_count == 1
    assert result.counts.stale_account_count == 0
    assert result.is_complete is True


def test_new_account_under_a_reauth_item_is_counted_on_both_independent_axes(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_run(db, "run-new-reauth")
    item_id = add_item(db, "new-reauth", status=ItemState.NEEDS_REAUTH)
    add_account(db, "new-reauth", item_id=item_id, reconciliation="NEW")

    result = Snapshotter(store).run("run-new-reauth", at=NOW)

    assert result.counts.reauth_account_count == 1
    assert result.counts.unreconciled_account_count == 1


def test_a_successful_run_missing_a_contributor_fails_instead_of_understating(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_run(db, "run-missing")
    item_id = add_item(db, "missing")
    add_account(db, "missing", item_id=item_id)

    with pytest.raises(SnapshotInputError, match="no observation in successful run"):
        Snapshotter(store).run("run-missing", at=NOW)

    assert store.snapshots.for_sync_run("run-missing") is None


def test_mixed_currency_input_fails_instead_of_summing_unlike_units(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    add_run(db, "run-mixed-currency")
    item_id = add_item(db, "mixed-currency")
    account_id = add_account(db, "mixed-currency", item_id=item_id)
    add_observation(store, "run-mixed-currency", account_id, 10_000, currency="EUR")

    with pytest.raises(SnapshotInputError, match="single-currency USD contribution"):
        Snapshotter(store).run("run-mixed-currency", at=NOW)

    assert store.snapshots.for_sync_run("run-mixed-currency") is None
