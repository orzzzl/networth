"""Task 10: hourly Item polling is the whole of Axis A's v0 input."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from networth.item_health import ItemHealthPoller
from networth.model import ItemState
from networth.plaid import (
    HEALTHY,
    ItemStatus,
    classify_error,
    malformed_response,
    transport_failure,
)
from networth.storage import migrate
from networth.store import Store

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
INITIAL = NOW - timedelta(days=2)
INVESTMENTS_UPDATE = NOW - timedelta(hours=3)


def _db_time(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    connection.execute(
        "INSERT INTO institution(plaid_institution_id, name, is_oauth) "
        "VALUES ('synthetic-institution', 'Synthetic institution', 0)"
    )
    try:
        yield connection
    finally:
        connection.close()


def add_item(
    connection: sqlite3.Connection,
    suffix: str,
    *,
    status: ItemState = ItemState.HEALTHY,
    status_since: datetime = INITIAL,
    last_polled_at: datetime | None = None,
    investments_update: datetime | None = None,
    last_error_code: str | None = None,
    last_error_detail: str | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO item(
            institution_id, plaid_item_id, secret_ref, status, status_since,
            last_error_code, last_error_message, created_at,
            last_health_poll_at, investments_last_successful_update
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"plaid-item-{suffix}",
            f"secret-ref-{suffix}",
            status.value,
            _db_time(status_since),
            last_error_code,
            last_error_detail,
            _db_time(INITIAL),
            None if last_polled_at is None else _db_time(last_polled_at),
            None if investments_update is None else _db_time(investments_update),
        ),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


class FakeSecret:
    def __init__(self, material: str) -> None:
        self._material = material

    def reveal(self) -> str:
        return self._material

    def __repr__(self) -> str:
        return "<FakeSecret: redacted>"


class FakeTokenResolver:
    def __init__(self, materials: dict[str, str]) -> None:
        self._materials = materials
        self.requests: list[str] = []

    def get(self, secret_ref: str) -> FakeSecret:
        self.requests.append(secret_ref)
        return FakeSecret(self._materials[secret_ref])


class FakeClient:
    def __init__(self, outcomes: dict[str, ItemStatus]) -> None:
        self._outcomes = outcomes
        self.calls: list[str] = []

    def item_get(self, access_token: str) -> ItemStatus:
        self.calls.append(access_token)
        return self._outcomes[access_token]


class TransactionCheckingClient(FakeClient):
    def __init__(
        self,
        outcomes: dict[str, ItemStatus],
        connection: sqlite3.Connection,
    ) -> None:
        super().__init__(outcomes)
        self._connection = connection

    def item_get(self, access_token: str) -> ItemStatus:
        assert not self._connection.in_transaction
        return super().item_get(access_token)


def item_status(
    suffix: str,
    state: ItemState,
    *,
    investments_observed: bool = False,
    investments_update: datetime | None = None,
) -> ItemStatus:
    if state is ItemState.HEALTHY:
        classification = HEALTHY
    elif state is ItemState.DEGRADED:
        classification = classify_error("INSTITUTION_NOT_RESPONDING", "INSTITUTION_ERROR")
    elif state is ItemState.NEEDS_REAUTH:
        classification = classify_error("ITEM_LOGIN_REQUIRED", "ITEM_ERROR")
    else:
        classification = classify_error("ITEM_NOT_FOUND", "ITEM_ERROR")
    return ItemStatus(
        item_id=f"plaid-item-{suffix}",
        classification=classification,
        request_id=f"request-{suffix}",
        investments_status_observed=investments_observed,
        investments_last_successful_update=investments_update,
    )


def poller_for(
    store: Store,
    outcomes: dict[str, ItemStatus],
) -> tuple[ItemHealthPoller, FakeClient, FakeTokenResolver]:
    materials = {f"secret-ref-{suffix}": f"material-{suffix}" for suffix in outcomes}
    client = FakeClient({f"material-{suffix}": result for suffix, result in outcomes.items()})
    tokens = FakeTokenResolver(materials)
    return ItemHealthPoller(store.items, client, tokens), client, tokens


def test_poll_all_persists_every_visible_axis_a_state(
    db: sqlite3.Connection,
) -> None:
    suffixes = {
        "healthy": ItemState.HEALTHY,
        "degraded": ItemState.DEGRADED,
        "reauth": ItemState.NEEDS_REAUTH,
        "revoked": ItemState.REVOKED,
    }
    ids = {suffix: add_item(db, suffix) for suffix in suffixes}
    store = Store(db)
    outcomes = {
        suffix: item_status(
            suffix,
            state,
            investments_observed=suffix == "healthy",
            investments_update=INVESTMENTS_UPDATE if suffix == "healthy" else None,
        )
        for suffix, state in suffixes.items()
    }
    poller, client, tokens = poller_for(store, outcomes)

    results = poller.poll_all(at=NOW)

    assert [result.id for result in results] == list(ids.values())
    assert client.calls == [f"material-{suffix}" for suffix in suffixes]
    assert tokens.requests == [f"secret-ref-{suffix}" for suffix in suffixes]
    for suffix, expected in suffixes.items():
        stored = store.items.get(ids[suffix])
        assert stored is not None
        assert stored.status is expected
        assert stored.last_polled_at == NOW
        assert stored.status_since == (INITIAL if expected is ItemState.HEALTHY else NOW)
        if expected is ItemState.HEALTHY:
            assert stored.last_error_code is None
            assert stored.last_error_detail is None
            assert stored.investments_last_successful_update == INVESTMENTS_UPDATE
        else:
            assert stored.last_error_detail is not None

    rendered = repr(results)
    assert all(material not in rendered for material in client.calls)


def test_due_boundary_polls_never_polled_and_exactly_one_hour_old_items(
    db: sqlite3.Connection,
) -> None:
    add_item(db, "never")
    add_item(db, "boundary", last_polled_at=NOW - timedelta(hours=1))
    add_item(
        db,
        "not-due",
        last_polled_at=NOW - timedelta(hours=1) + timedelta(microseconds=1),
    )
    store = Store(db)
    outcomes = {
        suffix: item_status(suffix, ItemState.HEALTHY)
        for suffix in ("never", "boundary", "not-due")
    }
    poller, client, _ = poller_for(store, outcomes)

    results = poller.poll_due(at=NOW)

    assert [result.plaid_item_id for result in results] == [
        "plaid-item-never",
        "plaid-item-boundary",
    ]
    assert client.calls == ["material-never", "material-boundary"]


def test_link_transition_may_be_newer_than_the_previous_health_poll(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(
        db,
        "relinked",
        status_since=NOW,
        last_polled_at=INITIAL,
    )

    store = Store(db)
    poller, _, _ = poller_for(
        store,
        {"relinked": item_status("relinked", ItemState.REVOKED)},
    )

    stored = poller.poll_item(item_id, at=NOW - timedelta(minutes=1))

    assert stored is not None
    assert stored.status is ItemState.HEALTHY
    assert stored.status_since == NOW
    assert stored.last_polled_at == INITIAL


def test_all_network_calls_finish_before_the_write_transaction_opens(
    db: sqlite3.Connection,
) -> None:
    for suffix in ("one", "two"):
        add_item(db, suffix)
    db.commit()
    store = Store(db)
    outcomes = {
        f"material-{suffix}": item_status(suffix, ItemState.HEALTHY) for suffix in ("one", "two")
    }
    client = TransactionCheckingClient(outcomes, db)
    tokens = FakeTokenResolver(
        {f"secret-ref-{suffix}": f"material-{suffix}" for suffix in ("one", "two")}
    )
    poller = ItemHealthPoller(store.items, client, tokens)

    poller.poll_all(at=NOW)

    assert client.calls == ["material-one", "material-two"]
    assert db.in_transaction


def test_pending_disconnect_is_not_a_poll_driven_transition(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(
        db,
        "pending",
        status=ItemState.NEEDS_REAUTH,
        last_error_code="ITEM_LOGIN_REQUIRED",
        last_error_detail="synthetic existing error",
    )
    store = Store(db)
    pending = ItemStatus(
        item_id="plaid-item-pending",
        classification=classify_error("PENDING_DISCONNECT", "ITEM_ERROR"),
        request_id="request-pending",
        investments_status_observed=True,
        investments_last_successful_update=INVESTMENTS_UPDATE,
    )
    poller, _, _ = poller_for(store, {"pending": pending})

    stored = poller.poll_item(item_id, at=NOW)

    assert stored.status is ItemState.NEEDS_REAUTH
    assert stored.status_since == INITIAL
    assert stored.last_error_code == "ITEM_LOGIN_REQUIRED"
    assert stored.last_error_detail == "synthetic existing error"
    assert stored.last_polled_at == NOW
    assert stored.investments_last_successful_update == INVESTMENTS_UPDATE


def test_transport_failure_preserves_the_last_observed_investments_clock(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "transport", investments_update=INVESTMENTS_UPDATE)
    store = Store(db)
    failure = ItemStatus(
        item_id=None,
        classification=transport_failure("synthetic transport failure"),
        request_id=None,
    )
    poller, _, _ = poller_for(store, {"transport": failure})

    stored = poller.poll_item(item_id, at=NOW)

    assert stored.status is ItemState.DEGRADED
    assert stored.investments_last_successful_update == INVESTMENTS_UPDATE
    assert stored.last_error_code is None
    assert stored.last_error_detail is not None


@pytest.mark.parametrize(
    ("prior_state", "prior_error_code"),
    [
        (ItemState.NEEDS_REAUTH, "ITEM_LOGIN_REQUIRED"),
        (ItemState.REVOKED, "ITEM_NOT_FOUND"),
    ],
)
@pytest.mark.parametrize("outcome_kind", ["transport", "malformed", "wrong-item"])
def test_no_item_state_evidence_cannot_demote_an_actionable_state(
    db: sqlite3.Connection,
    prior_state: ItemState,
    prior_error_code: str,
    outcome_kind: str,
) -> None:
    suffix = f"{prior_state.value.lower()}-{outcome_kind}"
    prior_error_detail = "synthetic existing actionable error"
    item_id = add_item(
        db,
        suffix,
        status=prior_state,
        investments_update=INVESTMENTS_UPDATE,
        last_error_code=prior_error_code,
        last_error_detail=prior_error_detail,
    )
    if outcome_kind == "transport":
        outcome = ItemStatus(
            item_id=None,
            classification=transport_failure("synthetic transport failure"),
            request_id=None,
        )
    elif outcome_kind == "malformed":
        outcome = ItemStatus(
            item_id=f"plaid-item-{suffix}",
            classification=malformed_response("synthetic unreadable response"),
            request_id=f"request-{suffix}",
        )
    else:
        outcome = ItemStatus(
            item_id="plaid-item-different",
            classification=HEALTHY,
            request_id=f"request-{suffix}",
            investments_status_observed=True,
            investments_last_successful_update=NOW,
        )
    store = Store(db)
    poller, _, _ = poller_for(store, {suffix: outcome})

    stored = poller.poll_item(item_id, at=NOW)

    assert stored.status is prior_state
    assert stored.status.owner_actionable
    assert stored.status_since == INITIAL
    assert stored.last_error_code == prior_error_code
    assert stored.last_error_detail == prior_error_detail
    assert stored.last_polled_at == NOW
    assert stored.investments_last_successful_update == INVESTMENTS_UPDATE


def test_observed_null_investments_clock_clears_to_unknown(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "unknown", investments_update=INVESTMENTS_UPDATE)
    store = Store(db)
    outcome = item_status(
        "unknown",
        ItemState.HEALTHY,
        investments_observed=True,
        investments_update=None,
    )
    poller, _, _ = poller_for(store, {"unknown": outcome})

    stored = poller.poll_item(item_id, at=NOW)

    assert stored.status is ItemState.HEALTHY
    assert stored.investments_last_successful_update is None


def test_response_for_a_different_item_cannot_mark_the_target_healthy(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(
        db,
        "expected",
        status=ItemState.DEGRADED,
        last_error_detail="synthetic prior failure",
    )
    store = Store(db)
    wrong = ItemStatus(
        item_id="plaid-item-different",
        classification=HEALTHY,
        request_id="request-wrong",
        investments_status_observed=True,
        investments_last_successful_update=INVESTMENTS_UPDATE,
    )
    poller, _, _ = poller_for(store, {"expected": wrong})

    stored = poller.poll_item(item_id, at=NOW)

    assert stored.status is ItemState.DEGRADED
    assert stored.status_since == INITIAL
    assert stored.last_error_detail is not None
    assert "did not match" in stored.last_error_detail
    assert stored.investments_last_successful_update is None


def test_healthy_response_without_item_identity_cannot_mark_the_target_healthy(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(
        db,
        "missing-identity",
        status=ItemState.DEGRADED,
        last_error_detail="synthetic prior failure",
    )
    store = Store(db)
    missing_identity = ItemStatus(
        item_id=None,
        classification=HEALTHY,
        request_id="request-missing-identity",
        investments_status_observed=True,
        investments_last_successful_update=INVESTMENTS_UPDATE,
    )
    poller, _, _ = poller_for(store, {"missing-identity": missing_identity})

    stored = poller.poll_item(item_id, at=NOW)

    assert stored.status is ItemState.DEGRADED
    assert stored.status_since == INITIAL
    assert stored.last_error_detail is not None
    assert "did not match" in stored.last_error_detail
    assert stored.investments_last_successful_update is None


def test_one_unresolvable_token_does_not_discard_or_block_other_item_polls(
    db: sqlite3.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ids = {suffix: add_item(db, suffix) for suffix in ("one", "broken", "three")}
    store = Store(db)
    client = FakeClient(
        {
            "material-one": item_status("one", ItemState.NEEDS_REAUTH),
            "material-three": item_status("three", ItemState.NEEDS_REAUTH),
        }
    )
    tokens = FakeTokenResolver(
        {
            "secret-ref-one": "material-one",
            "secret-ref-three": "material-three",
        }
    )
    poller = ItemHealthPoller(store.items, client, tokens)

    results = poller.poll_all(at=NOW)

    assert [result.id for result in results] == [ids["one"], ids["three"]]
    assert caplog.messages == [
        "1 Item poll attempt(s) produced no observation; skipped exception types: KeyError"
    ]
    assert "secret-ref-broken" not in caplog.text
    assert tokens.requests == ["secret-ref-one", "secret-ref-broken", "secret-ref-three"]
    assert client.calls == ["material-one", "material-three"]
    for suffix in ("one", "three"):
        stored = store.items.get(ids[suffix])
        assert stored is not None
        assert stored.status is ItemState.NEEDS_REAUTH
        assert stored.status_since == NOW
        assert stored.last_polled_at == NOW

    broken = store.items.get(ids["broken"])
    assert broken is not None
    assert broken.status is ItemState.HEALTHY
    assert broken.status_since == INITIAL
    assert broken.last_polled_at is None


def test_late_arriving_older_poll_cannot_overwrite_newer_health(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "ordered")
    store = Store(db)
    newer_poller, _, _ = poller_for(
        store,
        {"ordered": item_status("ordered", ItemState.NEEDS_REAUTH)},
    )
    older_poller, _, _ = poller_for(
        store,
        {"ordered": item_status("ordered", ItemState.REVOKED)},
    )

    newer = newer_poller.poll_item(item_id, at=NOW)
    older = older_poller.poll_item(item_id, at=NOW - timedelta(minutes=1))

    assert newer.status is ItemState.NEEDS_REAUTH
    assert older == newer
    assert store.items.get(item_id) == newer
