"""Task 15: the alert repository, and the two guarantees migration 0004 adds."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from networth.model import AlertDraft, AlertKind
from networth.storage import migrate
from networth.store import (
    AlertAlreadyOpenError,
    AlertNotFoundError,
    AlertRepository,
    Store,
    StoredDataError,
)

NOW = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
SOURCE_AS_OF = datetime(2026, 3, 2, 21, 0, tzinfo=UTC)


def _db_time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    connection.execute(
        "INSERT INTO institution(id, plaid_institution_id, name, is_oauth) "
        "VALUES (1, 'ins-1', 'Synthetic', 0)"
    )
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def alerts(db: sqlite3.Connection) -> AlertRepository:
    return Store(db).alerts


def add_item(connection: sqlite3.Connection, suffix: str) -> int:
    cursor = connection.execute(
        """
        INSERT INTO item(
            institution_id, plaid_item_id, secret_ref, status, status_since, created_at
        ) VALUES (1, ?, ?, 'HEALTHY', ?, ?)
        """,
        (f"plaid-item-{suffix}", f"secret-ref-{suffix}", _db_time(NOW), _db_time(NOW)),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def add_account(connection: sqlite3.Connection, name: str) -> int:
    cursor = connection.execute(
        """
        INSERT INTO account(
            name, type, currency, sign, freshness_policy,
            include_in_net_worth, reconciliation_state, created_at
        ) VALUES (?, 'synthetic', 'USD', 1, 'SYNCED_BALANCE', 1, 'CONFIRMED', ?)
        """,
        (name, _db_time(NOW)),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def reauth_draft(item_id: int, *, at: datetime = NOW) -> AlertDraft:
    return AlertDraft(
        kind=AlertKind.NEEDS_REAUTH,
        created_at=at,
        message="synthetic reauth alert",
        item_id=item_id,
    )


def frozen_draft(
    account_id: int,
    *,
    at: datetime = NOW,
    raised_for: datetime = SOURCE_AS_OF,
) -> AlertDraft:
    return AlertDraft(
        kind=AlertKind.FROZEN_DATA,
        created_at=at,
        message="synthetic frozen alert",
        account_id=account_id,
        raised_source_as_of=raised_for,
    )


def test_an_alert_round_trips_with_every_clock_it_was_given(
    db: sqlite3.Connection,
    alerts: AlertRepository,
) -> None:
    account_id = add_account(db, "Frozen account")

    stored = alerts.raise_alert(frozen_draft(account_id))

    assert stored.id > 0
    assert stored.kind is AlertKind.FROZEN_DATA
    assert stored.account_id == account_id
    assert stored.item_id is None
    assert stored.raised_source_as_of == SOURCE_AS_OF
    assert stored.notified_at is None
    assert stored.acknowledged_at is None
    assert stored.resolved_at is None
    assert stored.is_open
    assert alerts.get(stored.id) == stored


def test_a_second_open_alert_for_the_same_subject_is_refused_by_the_index(
    db: sqlite3.Connection,
    alerts: AlertRepository,
) -> None:
    """Section 11's "one alert per item per state entry", enforced by the table.

    Raised through the repository rather than the evaluator on purpose: the
    guarantee has to hold for *any* writer, not only for the one that remembers
    to check first.
    """

    item_id = add_item(db, "reauth")
    alerts.raise_alert(reauth_draft(item_id))

    with pytest.raises(AlertAlreadyOpenError):
        alerts.raise_alert(reauth_draft(item_id, at=NOW + timedelta(hours=1)))

    assert len(alerts.open()) == 1


def test_the_same_subject_may_alert_again_after_the_first_one_is_resolved(
    db: sqlite3.Connection,
    alerts: AlertRepository,
) -> None:
    item_id = add_item(db, "recovered")
    first = alerts.raise_alert(reauth_draft(item_id))
    alerts.resolve(first.id, at=NOW + timedelta(hours=1))

    second = alerts.raise_alert(reauth_draft(item_id, at=NOW + timedelta(hours=2)))

    assert second.id != first.id
    assert [alert.id for alert in alerts.open()] == [second.id]


def test_two_subjects_of_the_same_kind_do_not_collide(
    db: sqlite3.Connection,
    alerts: AlertRepository,
) -> None:
    first = add_item(db, "one")
    second = add_item(db, "two")

    alerts.raise_alert(reauth_draft(first))
    alerts.raise_alert(reauth_draft(second))

    assert {alert.item_id for alert in alerts.open()} == {first, second}


def test_the_open_set_is_ordered_by_when_each_alert_was_raised(
    db: sqlite3.Connection,
    alerts: AlertRepository,
) -> None:
    older = add_item(db, "older")
    newer = add_item(db, "newer")
    second = alerts.raise_alert(reauth_draft(newer, at=NOW + timedelta(hours=5)))
    first = alerts.raise_alert(reauth_draft(older, at=NOW))

    assert [alert.id for alert in alerts.open()] == [first.id, second.id]


def test_a_resolved_alert_leaves_the_open_set_and_keeps_its_resolution_time(
    db: sqlite3.Connection,
    alerts: AlertRepository,
) -> None:
    item_id = add_item(db, "resolving")
    stored = alerts.raise_alert(reauth_draft(item_id))
    resolved_at = NOW + timedelta(hours=3)

    resolved = alerts.resolve(stored.id, at=resolved_at)

    assert resolved.resolved_at == resolved_at
    assert not resolved.is_open
    assert alerts.open() == ()
    assert alerts.get(stored.id) == resolved


def test_resolving_twice_is_refused_rather_than_moving_the_record(
    db: sqlite3.Connection,
    alerts: AlertRepository,
) -> None:
    item_id = add_item(db, "twice")
    stored = alerts.raise_alert(reauth_draft(item_id))
    alerts.resolve(stored.id, at=NOW + timedelta(hours=1))

    with pytest.raises(AlertNotFoundError):
        alerts.resolve(stored.id, at=NOW + timedelta(hours=2))

    reread = alerts.get(stored.id)
    assert reread is not None
    assert reread.resolved_at == NOW + timedelta(hours=1)


def test_a_notification_stamp_cannot_move_backwards(
    db: sqlite3.Connection,
    alerts: AlertRepository,
) -> None:
    """The 24-hour window is measured from this value, so an out-of-order write
    would silently buy a second prompt inside one window."""

    item_id = add_item(db, "stamped")
    stored = alerts.raise_alert(reauth_draft(item_id))
    alerts.mark_notified(stored.id, at=NOW + timedelta(hours=6))

    with pytest.raises(ValueError, match="backwards"):
        alerts.mark_notified(stored.id, at=NOW + timedelta(hours=1))


def test_a_stamp_older_than_the_alert_is_refused(
    db: sqlite3.Connection,
    alerts: AlertRepository,
) -> None:
    item_id = add_item(db, "prehistoric")
    stored = alerts.raise_alert(reauth_draft(item_id))

    with pytest.raises(ValueError, match="cannot precede"):
        alerts.mark_notified(stored.id, at=NOW - timedelta(minutes=1))


def test_lifecycle_writes_name_the_alert_that_does_not_exist(
    alerts: AlertRepository,
) -> None:
    with pytest.raises(AlertNotFoundError):
        alerts.resolve(404, at=NOW)
    assert alerts.get(404) is None


def test_an_alert_cannot_name_a_subject_that_does_not_exist(
    db: sqlite3.Connection,
    alerts: AlertRepository,
) -> None:
    """The foreign key is real here, and it is not reported as "already open".

    Both faults arrive as ``sqlite3.IntegrityError``.  Translating every one of
    them into :class:`AlertAlreadyOpenError` — which this repository did until
    this test was written — would send a reader looking for an open alert that
    never existed, for a subject that also never existed.
    """

    with pytest.raises(sqlite3.IntegrityError) as raised:
        alerts.raise_alert(reauth_draft(4242))
    assert not isinstance(raised.value, AlertAlreadyOpenError)
    assert raised.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY
    db.rollback()


def test_a_stored_frozen_alert_without_its_source_clock_is_refused_on_read(
    db: sqlite3.Connection,
    alerts: AlertRepository,
) -> None:
    """The column is nullable, so the invariant has to survive a foreign writer.

    Written with raw SQL precisely because the repository would not produce
    this row: a FROZEN_DATA alert with no ``raised_source_as_of`` cannot say
    what its source clock must advance past, and would silently never resolve.
    """

    account_id = add_account(db, "Hand-written")
    db.execute(
        """
        INSERT INTO alert(kind, created_at, message, account_id)
        VALUES ('FROZEN_DATA', ?, 'hand-written', ?)
        """,
        (_db_time(NOW), account_id),
    )

    with pytest.raises(StoredDataError):
        alerts.open()


def test_a_stored_alert_claiming_both_subjects_is_refused_on_read(
    db: sqlite3.Connection,
    alerts: AlertRepository,
) -> None:
    item_id = add_item(db, "both")
    account_id = add_account(db, "Both")
    db.execute(
        """
        INSERT INTO alert(kind, created_at, message, item_id, account_id)
        VALUES ('NEEDS_REAUTH', ?, 'hand-written', ?, ?)
        """,
        (_db_time(NOW), item_id, account_id),
    )

    with pytest.raises(StoredDataError):
        alerts.open()


def test_the_repository_refuses_a_connection_without_foreign_keys() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        migrate(connection)
        connection.execute("PRAGMA foreign_keys = OFF")
        with pytest.raises(Exception, match="foreign_keys"):
            AlertRepository(connection)
    finally:
        connection.close()


def test_the_store_facade_exposes_the_alert_repository(db: sqlite3.Connection) -> None:
    assert isinstance(Store(db).alerts, AlertRepository)


def test_drafts_and_ids_are_type_checked(alerts: AlertRepository) -> None:
    with pytest.raises(TypeError):
        alerts.raise_alert("NEEDS_REAUTH")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        # `bool` is an `int` to the type checker and a trap at runtime: True
        # would silently address alert 1.
        alerts.resolve(True, at=NOW)
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        alerts.mark_notified(1, at=NOW.replace(tzinfo=None))
