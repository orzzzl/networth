"""Task 19: one honest payload, encrypted and replaced atomically in SQLite."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from networth.model import (
    AlertDraft,
    AlertKind,
    FreshnessPolicy,
    ObservationDraft,
    ObservationSource,
    SourcedFigure,
)
from networth.payload import PayloadEnvelope, open_payload
from networth.publisher import (
    GRACE_SECONDS,
    PUBLISH_INTERVAL_SECONDS,
    PairingUnavailableError,
    Publisher,
    PublisherError,
    PublisherTransactionError,
)
from networth.query import NetWorthQueryError
from networth.snapshotter import Snapshotter
from networth.storage import migrate
from networth.store import Store

NOW = datetime(2026, 9, 14, 19, 0, tzinfo=UTC)
SOURCE_AS_OF = NOW - timedelta(hours=1)
PAIRING_ID = "00000000-0000-4000-8000-000000000019"
KEY_REF = "payload-key/synthetic-pairing"
KEY = bytes(range(32))


class Nonces:
    """Deterministic, unique nonces for assertions without random fixtures."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, size: int) -> bytes:
        assert size == 12
        self.calls += 1
        return bytes((self.calls,)) * size


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _setup_snapshot(
    connection: sqlite3.Connection,
    *,
    alert: bool = False,
    is_carried_forward: bool = False,
    source_as_of: datetime | None = SOURCE_AS_OF,
) -> tuple[int, int]:
    institution = connection.execute(
        "INSERT INTO institution(plaid_institution_id, name, is_oauth) VALUES (?, ?, 0)",
        ("synthetic-institution", "Synthetic institution"),
    )
    assert institution.lastrowid is not None
    item = connection.execute(
        """
        INSERT INTO item(
            institution_id, plaid_item_id, secret_ref, status, status_since, created_at
        ) VALUES (?, ?, ?, 'HEALTHY', ?, ?)
        """,
        (
            int(institution.lastrowid),
            "synthetic-item",
            "SYNTHETIC_TOKEN_REF",
            _timestamp(NOW - timedelta(days=1)),
            _timestamp(NOW - timedelta(days=2)),
        ),
    )
    assert item.lastrowid is not None
    item_id = int(item.lastrowid)
    account = connection.execute(
        """
        INSERT INTO account(
            item_id, plaid_account_id, name, type, currency, sign,
            freshness_policy, include_in_net_worth, reconciliation_state, created_at
        ) VALUES (?, ?, ?, 'synthetic', 'USD', 1, ?, 1, 'CONFIRMED', ?)
        """,
        (
            item_id,
            "synthetic-account",
            "Synthetic account",
            FreshnessPolicy.SYNCED_BALANCE.value,
            _timestamp(NOW - timedelta(days=2)),
        ),
    )
    assert account.lastrowid is not None
    account_id = int(account.lastrowid)
    connection.execute(
        """
        INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok, error_summary)
        VALUES ('run-publisher', ?, ?, 'TEST', 1, NULL)
        """,
        (_timestamp(NOW - timedelta(minutes=1)), _timestamp(NOW)),
    )
    store = Store(connection)
    store.observations.append(
        ObservationDraft(
            sync_run_id="run-publisher",
            account_id=account_id,
            observed_at=NOW,
            figure=SourcedFigure(
                value_minor=12_345,
                currency="USD",
                as_of=source_as_of,
                source_clock=("UNKNOWN" if source_as_of is None else "SYNTHETIC_BALANCE_CLOCK"),
            ),
            source=ObservationSource.PLAID_BALANCE,
            fetched_at=NOW,
            is_carried_forward=is_carried_forward,
        )
    )
    snapshot = Snapshotter(store).run("run-publisher", at=NOW)
    connection.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) VALUES (?, ?, ?, 'ACTIVE')",
        (PAIRING_ID, _timestamp(NOW), KEY_REF),
    )
    if alert:
        store.alerts.raise_alert(
            AlertDraft(
                kind=AlertKind.NEEDS_REAUTH,
                created_at=NOW,
                message="Reconnect the affected connection.",
                item_id=item_id,
            )
        )
    connection.commit()
    return snapshot.id, account_id


def _publisher(connection: sqlite3.Connection, nonces: Nonces | None = None) -> Publisher:
    def resolve(reference: str) -> bytes:
        assert reference == KEY_REF
        return KEY

    return Publisher(connection, resolve, nonce_factory=nonces or Nonces())


def _document(envelope: PayloadEnvelope, key: bytes = KEY) -> dict[str, Any]:
    decoded = json.loads(open_payload(envelope, key))
    assert isinstance(decoded, dict)
    return cast(dict[str, Any], decoded)


def _stored_envelope(connection: sqlite3.Connection) -> tuple[object, ...]:
    row = connection.execute(
        """
        SELECT publication_id, pairing_id, schema_version, seq, published_at,
               nonce, ciphertext, is_active
        FROM published_envelope
        """
    ).fetchone()
    assert row is not None
    return cast(tuple[object, ...], row)


def test_publication_carries_the_complete_first_schema_and_stamps_prompted_alert(
    db: sqlite3.Connection,
) -> None:
    snapshot_id, account_id = _setup_snapshot(db, alert=True)
    publisher = _publisher(db)

    publication = publisher.publish(at=NOW)
    document = _document(publication.envelope)

    assert publication.snapshot_id == snapshot_id
    assert publication.seq == 1
    assert document["schema_version"] == publication.envelope.schema_version == "1"
    assert document["pairing_id"] == publication.envelope.pairing_id == PAIRING_ID
    assert document["seq"] == publication.envelope.seq == "1"
    assert document["published_at"] == publication.envelope.published_at == _timestamp(NOW)
    assert document["publish_interval_seconds"] == PUBLISH_INTERVAL_SECONDS == 86_400
    assert document["grace_seconds"] == GRACE_SECONDS == 21_600
    assert document["connection_state"] == "OK"
    assert document["total"] == {
        "account_count": 1,
        "age_state": "KNOWN",
        "as_of": _timestamp(SOURCE_AS_OF),
        "assets_minor": 12_345,
        "currency": "USD",
        "is_complete": True,
        "liabilities_minor": 0,
        "oldest_known_source_as_of": _timestamp(SOURCE_AS_OF),
        "reauth_account_count": 0,
        "stale_account_count": 0,
        "static_account_count": 0,
        "unknown_freshness_account_count": 0,
        "unreconciled_account_count": 0,
        "value_minor": 12_345,
    }
    assert document["accounts"] == [
        {
            "account_id": account_id,
            "currency": "USD",
            "freshness": {
                "as_of": _timestamp(SOURCE_AS_OF),
                "is_carried_forward": False,
                "market_days_without_advance": 0,
                "state": "FRESH",
            },
            "item_state": "HEALTHY",
            "reconciliation_state": "CONFIRMED",
            "sign": 1,
            "value_minor": 12_345,
        }
    ]
    assert document["item_budget"] == {
        "capacity": 10,
        "in_flight_count": 0,
        "orphaned_count": 0,
        "remaining": 9,
        "replacement_count": 0,
        "spent_count": 1,
        "state": "AVAILABLE",
        "stranded_count": 0,
        "usable_count": 1,
    }
    assert document["alerts"] == [
        {
            "kind": "NEEDS_REAUTH",
            "message": "Reconnect the affected connection.",
            "prompt": True,
            "subject": {"id": 1, "kind": "ITEM"},
        }
    ]
    assert db.execute("SELECT notified_at FROM alert").fetchone() == (_timestamp(NOW),)

    assert db.execute(
        """
        SELECT snapshot_id, pairing_id, seq, schema_version, published_at, ok, error
        FROM publication
        """
    ).fetchone() == (snapshot_id, PAIRING_ID, 1, "1", _timestamp(NOW), 1, None)

    stored = _stored_envelope(db)
    assert stored == (
        publication.id,
        PAIRING_ID,
        "1",
        "1",
        _timestamp(NOW),
        publication.envelope.nonce,
        publication.envelope.payload,
        1,
    )
    plaintext = open_payload(publication.envelope, KEY)
    assert KEY_REF.encode() not in plaintext
    assert b"synthetic-item" not in plaintext


def test_unknown_total_age_keeps_its_tag_and_never_invents_a_date(
    db: sqlite3.Connection,
) -> None:
    _setup_snapshot(db, source_as_of=None)

    total = _document(_publisher(db).publish(at=NOW).envelope)["total"]

    assert total["age_state"] == "UNKNOWN"
    assert total["as_of"] is None
    assert total["oldest_known_source_as_of"] is None
    assert total["unknown_freshness_account_count"] == 1


def test_publication_preserves_non_benign_honesty_fields_and_account_alert_subject(
    db: sqlite3.Connection,
) -> None:
    _, account_id = _setup_snapshot(
        db,
        is_carried_forward=True,
        source_as_of=NOW - timedelta(days=45),
    )
    Store(db).alerts.raise_alert(
        AlertDraft(
            kind=AlertKind.PENDING_RECONCILIATION,
            created_at=NOW,
            message="Confirm the synthetic account mapping.",
            account_id=account_id,
        )
    )
    db.commit()

    document = _document(_publisher(db).publish(at=NOW).envelope)

    assert document["total"]["is_complete"] is False
    assert document["total"]["stale_account_count"] == 1
    assert document["connection_state"] == "ACTION_NEEDED"
    assert document["accounts"][0]["freshness"] == {
        "as_of": _timestamp(NOW - timedelta(days=45)),
        "is_carried_forward": True,
        "market_days_without_advance": 30,
        "state": "FROZEN",
    }
    assert document["alerts"] == [
        {
            "kind": "PENDING_RECONCILIATION",
            "message": "Confirm the synthetic account mapping.",
            "prompt": True,
            "subject": {"id": account_id, "kind": "ACCOUNT"},
        }
    ]


def test_empty_snapshot_is_tagged_static_only_instead_of_dated_at_publication(
    db: sqlite3.Connection,
) -> None:
    db.execute(
        """
        INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok, error_summary)
        VALUES ('run-empty', ?, ?, 'TEST', 1, NULL)
        """,
        (_timestamp(NOW - timedelta(minutes=1)), _timestamp(NOW)),
    )
    db.execute(
        """
        INSERT INTO snapshot(
            sync_run_id, taken_at, total_net_worth_minor, total_assets_minor,
            total_liabilities_minor, account_count, stale_account_count,
            unknown_freshness_account_count, static_account_count,
            reauth_account_count, unreconciled_account_count, is_complete,
            age_state, as_of, oldest_known_source_as_of
        ) VALUES ('run-empty', ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1,
                  'STATIC_ONLY', NULL, NULL)
        """,
        (_timestamp(NOW),),
    )
    db.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) VALUES (?, ?, ?, 'ACTIVE')",
        (PAIRING_ID, _timestamp(NOW), KEY_REF),
    )
    db.commit()

    total = _document(_publisher(db).publish(at=NOW).envelope)["total"]

    assert total["age_state"] == "STATIC_ONLY"
    assert total["as_of"] is None
    assert total["oldest_known_source_as_of"] is None


def test_second_publication_replaces_only_the_ciphertext_and_keeps_the_audit_rows(
    db: sqlite3.Connection,
) -> None:
    _setup_snapshot(db, alert=True)
    publisher = _publisher(db, Nonces())
    first = publisher.publish(at=NOW)

    second = publisher.publish(at=NOW + timedelta(hours=1))

    assert first.seq == 1
    assert second.seq == 2
    assert first.envelope.nonce != second.envelope.nonce
    assert db.execute("SELECT count(*) FROM publication").fetchone() == (2,)
    assert db.execute("SELECT count(*) FROM published_envelope").fetchone() == (1,)
    assert _stored_envelope(db)[0] == second.id
    assert _document(second.envelope)["alerts"][0]["prompt"] is False
    assert db.execute("SELECT notified_at FROM alert").fetchone() == (_timestamp(NOW),)


def test_default_nonce_is_fresh_per_publication(db: sqlite3.Connection) -> None:
    _setup_snapshot(db)
    publisher = Publisher(db, lambda _reference: KEY)

    first = publisher.publish(at=NOW)
    second = publisher.publish(at=NOW + timedelta(hours=1))

    assert len(first.envelope.nonce) == 12
    assert first.envelope.nonce != second.envelope.nonce


def test_population_refusal_keeps_sequence_and_last_envelope_unchanged(
    db: sqlite3.Connection,
) -> None:
    _setup_snapshot(db)
    publisher = _publisher(db, Nonces())
    first = publisher.publish(at=NOW)
    before = _stored_envelope(db)
    db.execute(
        """
        INSERT INTO account(
            item_id, plaid_account_id, name, type, currency, sign,
            freshness_policy, include_in_net_worth, reconciliation_state, created_at
        ) VALUES (1, 'synthetic-new', 'Synthetic new', 'synthetic', 'USD', 1,
                  'SYNCED_BALANCE', 1, 'NEW', ?)
        """,
        (_timestamp(NOW),),
    )
    db.commit()

    with pytest.raises(NetWorthQueryError, match="population no longer matches"):
        publisher.publish(at=NOW + timedelta(hours=1))

    assert db.execute("SELECT max(seq), count(*) FROM publication").fetchone() == (1, 1)
    assert _stored_envelope(db) == before
    assert _stored_envelope(db)[0] == first.id


def test_failed_insert_after_delete_rolls_back_to_the_previous_envelope(
    db: sqlite3.Connection,
) -> None:
    _setup_snapshot(db)
    publisher = _publisher(db, Nonces())
    first = publisher.publish(at=NOW)
    before = _stored_envelope(db)
    db.execute(
        """
        CREATE TEMP TRIGGER synthetic_refuse_envelope
        BEFORE INSERT ON published_envelope
        BEGIN
            SELECT raise(ABORT, 'synthetic envelope refusal');
        END
        """
    )
    db.commit()

    with pytest.raises(sqlite3.IntegrityError, match="synthetic envelope refusal"):
        publisher.publish(at=NOW + timedelta(hours=1))

    assert db.execute("SELECT max(seq), count(*) FROM publication").fetchone() == (1, 1)
    assert _stored_envelope(db) == before
    assert _stored_envelope(db)[0] == first.id


def test_budget_refusal_survives_the_wire_as_a_tag_without_an_integer_fallback(
    db: sqlite3.Connection,
) -> None:
    _setup_snapshot(db)
    db.execute(
        """
        INSERT INTO link_flow(flow_id, minted_at, hosted_url_expires_at, state, item_id)
        VALUES ('synthetic-flow', ?, ?, 'EXCHANGED', NULL)
        """,
        (_timestamp(NOW), _timestamp(NOW + timedelta(hours=4))),
    )
    db.commit()

    publication = _publisher(db).publish(at=NOW)
    budget = _document(publication.envelope)["item_budget"]

    assert budget == {
        "reason": "STORED_EVIDENCE_INCONSISTENT",
        "state": "UNAVAILABLE",
    }
    assert "remaining" not in budget


def test_stranded_and_in_flight_slot_evidence_remain_distinguishable_in_payload(
    db: sqlite3.Connection,
) -> None:
    _setup_snapshot(db)
    for suffix, state in (("stranded", "TOKEN_EXPIRED"), ("pending", "EXCHANGING")):
        db.execute(
            """
            INSERT INTO link_flow(flow_id, minted_at, hosted_url_expires_at, state)
            VALUES (?, ?, ?, ?)
            """,
            (suffix, _timestamp(NOW), _timestamp(NOW + timedelta(hours=4)), state),
        )
    db.commit()

    budget = _document(_publisher(db).publish(at=NOW).envelope)["item_budget"]

    assert budget["spent_count"] == 3
    assert budget["remaining"] == 7
    assert budget["usable_count"] == 1
    assert budget["stranded_count"] == 1
    assert budget["in_flight_count"] == 1


def test_sequence_is_global_across_pairings_and_never_uses_publish_epoch(
    db: sqlite3.Connection,
) -> None:
    _setup_snapshot(db)
    keys = {KEY_REF: KEY, "payload-key/second": bytes(reversed(KEY))}
    publisher = Publisher(db, keys.__getitem__, nonce_factory=Nonces())
    db.execute(
        "UPDATE daemon_state SET publish_epoch = 999, epoch_bumped_at = ?, "
        "epoch_bumped_reason = 'synthetic restore' WHERE id = 1",
        (_timestamp(NOW),),
    )
    db.commit()
    first = publisher.publish(at=NOW)

    db.execute("DELETE FROM published_envelope")
    db.execute(
        "UPDATE pairing SET state = 'REVOKED', revoked_at = ? WHERE id = ?",
        (_timestamp(NOW + timedelta(minutes=1)), PAIRING_ID),
    )
    db.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) VALUES (?, ?, ?, 'ACTIVE')",
        ("00000000-0000-4000-8000-000000000020", _timestamp(NOW), "payload-key/second"),
    )
    db.commit()
    second = publisher.publish(at=NOW + timedelta(hours=1))

    assert first.seq == 1
    assert second.seq == 2
    assert second.envelope.pairing_id == "00000000-0000-4000-8000-000000000020"


def test_sequence_advances_when_the_wall_clock_moves_backwards(db: sqlite3.Connection) -> None:
    _setup_snapshot(db)
    publisher = _publisher(db, Nonces())
    first = publisher.publish(at=NOW + timedelta(days=1))

    second = publisher.publish(at=NOW)

    assert first.seq == 1
    assert second.seq == 2
    assert second.envelope.published_at < first.envelope.published_at


def test_publisher_does_not_read_back_the_row_it_just_wrote(db: sqlite3.Connection) -> None:
    _setup_snapshot(db)
    statements: list[str] = []
    db.set_trace_callback(statements.append)
    try:
        _publisher(db).publish(at=NOW)
    finally:
        db.set_trace_callback(None)

    insert_at = next(
        index
        for index, statement in enumerate(statements)
        if statement.lstrip().upper().startswith("INSERT INTO PUBLISHED_ENVELOPE")
    )
    read_backs = [
        statement
        for statement in statements[insert_at + 1 :]
        if statement.lstrip().upper().startswith("SELECT")
        and ("PUBLISHED_ENVELOPE" in statement.upper() or "PUBLICATION" in statement.upper())
    ]
    assert read_backs == []


def test_publisher_requires_exactly_one_active_pairing(db: sqlite3.Connection) -> None:
    _setup_snapshot(db)
    db.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) VALUES (?, ?, ?, 'ACTIVE')",
        ("00000000-0000-4000-8000-000000000020", _timestamp(NOW), "payload-key/second"),
    )
    db.commit()

    with pytest.raises(PairingUnavailableError, match="found 2"):
        _publisher(db).publish(at=NOW)

    assert db.execute("SELECT count(*) FROM publication").fetchone() == (0,)


def test_publisher_refuses_to_hide_its_commit_inside_a_callers_transaction(
    db: sqlite3.Connection,
) -> None:
    _setup_snapshot(db)
    db.execute("UPDATE daemon_state SET publish_epoch = 1 WHERE id = 1")

    with pytest.raises(PublisherTransactionError, match="no transaction"):
        _publisher(db).publish(at=NOW)

    assert db.in_transaction
    db.rollback()


def test_no_snapshot_is_a_refusal_not_an_empty_published_total(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) VALUES (?, ?, ?, 'ACTIVE')",
        (PAIRING_ID, _timestamp(NOW), KEY_REF),
    )
    db.commit()

    with pytest.raises(PublisherError, match="no snapshot"):
        _publisher(db).publish(at=NOW)

    assert db.execute("SELECT count(*) FROM publication").fetchone() == (0,)
