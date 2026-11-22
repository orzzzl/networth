"""Task 19: one honest payload, encrypted and replaced atomically in SQLite."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from networth.model import (
    MANUAL_VALUED_AS_OF,
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
    HISTORY_YEARS,
    OWNER_ZONE,
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
        SELECT snapshot_id, pairing_id, seq, schema_version, published_at
        FROM publication
        """
    ).fetchone() == (snapshot_id, PAIRING_ID, 1, "1", _timestamp(NOW))

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


def test_multi_account_wire_preserves_static_reauth_unreconciled_and_new_state(
    db: sqlite3.Connection,
) -> None:
    """Task 20's four carried fields each fail here if replaced by a benign constant."""

    revision_at = NOW - timedelta(hours=2)
    db.executemany(
        """
        INSERT INTO institution(id, plaid_institution_id, name, is_oauth)
        VALUES (?, ?, ?, 0)
        """,
        [
            (1, "synthetic-healthy-provider", "Synthetic healthy provider"),
            (2, "synthetic-reauth-provider", "Synthetic reauth provider"),
        ],
    )
    db.executemany(
        """
        INSERT INTO item(
            id, institution_id, plaid_item_id, secret_ref, status, status_since, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                1,
                1,
                "synthetic-healthy-item",
                "SYNTHETIC_HEALTHY_REF",
                "HEALTHY",
                _timestamp(revision_at),
                _timestamp(revision_at),
            ),
            (
                2,
                2,
                "synthetic-reauth-item",
                "SYNTHETIC_REAUTH_REF",
                "NEEDS_REAUTH",
                _timestamp(revision_at),
                _timestamp(revision_at),
            ),
        ],
    )
    db.executemany(
        """
        INSERT INTO account(
            id, item_id, plaid_account_id, name, type, currency, sign,
            freshness_policy, include_in_net_worth, reconciliation_state, created_at
        ) VALUES (?, ?, ?, ?, 'synthetic', 'USD', 1, ?, 1, ?, ?)
        """,
        [
            (
                1,
                1,
                "synthetic-confirmed-account",
                "Synthetic confirmed account",
                FreshnessPolicy.SYNCED_BALANCE.value,
                "CONFIRMED",
                _timestamp(revision_at),
            ),
            (
                2,
                2,
                "synthetic-new-account",
                "Synthetic new account",
                FreshnessPolicy.SYNCED_BALANCE.value,
                "NEW",
                _timestamp(revision_at),
            ),
            (
                3,
                None,
                None,
                "Synthetic fixed valuation",
                FreshnessPolicy.MANUAL_STATIC.value,
                "CONFIRMED",
                _timestamp(revision_at),
            ),
        ],
    )
    db.executemany(
        """
        INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok, error_summary)
        VALUES (?, ?, ?, 'TEST', 1, NULL)
        """,
        [
            ("run-manual-revision", _timestamp(revision_at), _timestamp(revision_at)),
            ("run-multi-account", _timestamp(NOW - timedelta(minutes=1)), _timestamp(NOW)),
        ],
    )
    store = Store(db)
    store.observations.append(
        ObservationDraft(
            sync_run_id="run-manual-revision",
            account_id=3,
            observed_at=revision_at,
            figure=SourcedFigure(
                value_minor=40_000,
                currency="USD",
                as_of=revision_at,
                source_clock=MANUAL_VALUED_AS_OF,
            ),
            source=ObservationSource.MANUAL,
            fetched_at=revision_at,
            is_carried_forward=False,
        )
    )
    store.observations.append(
        ObservationDraft(
            sync_run_id="run-multi-account",
            account_id=1,
            observed_at=NOW,
            figure=SourcedFigure(
                value_minor=20_000,
                currency="USD",
                as_of=SOURCE_AS_OF,
                source_clock="SYNTHETIC_BALANCE_CLOCK",
            ),
            source=ObservationSource.PLAID_BALANCE,
            fetched_at=NOW,
            is_carried_forward=False,
        )
    )
    Snapshotter(store).run("run-multi-account", at=NOW)
    db.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) VALUES (?, ?, ?, 'ACTIVE')",
        (PAIRING_ID, _timestamp(NOW), KEY_REF),
    )
    db.commit()

    document = _document(_publisher(db).publish(at=NOW).envelope)

    assert document["total"]["static_account_count"] == 1
    assert document["total"]["reauth_account_count"] == 1
    assert document["total"]["unreconciled_account_count"] == 1
    pending = next(account for account in document["accounts"] if account["account_id"] == 2)
    assert pending["reconciliation_state"] == "NEW"
    assert pending["item_state"] == "NEEDS_REAUTH"
    assert pending["value_minor"] is None
    assert pending["freshness"] is None


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


def _insert_snapshot(
    connection: sqlite3.Connection,
    run_id: str,
    taken_at: datetime,
    *,
    value_minor: int = 10_000,
    account_count: int = 1,
    is_complete: bool = True,
) -> None:
    """An earlier stored snapshot, written as rows rather than through a sync.

    Only the *latest* snapshot is checked against the live account population
    (`NetWorthQuery.latest`), so these must all predate `_setup_snapshot`'s.
    """

    assert taken_at < NOW
    connection.execute(
        """
        INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok, error_summary)
        VALUES (?, ?, ?, 'TEST', 1, NULL)
        """,
        (run_id, _timestamp(taken_at - timedelta(minutes=1)), _timestamp(taken_at)),
    )
    as_of = None if account_count == 0 else _timestamp(taken_at - timedelta(hours=1))
    connection.execute(
        """
        INSERT INTO snapshot(
            sync_run_id, taken_at, total_net_worth_minor, total_assets_minor,
            total_liabilities_minor, account_count, stale_account_count,
            unknown_freshness_account_count, static_account_count,
            reauth_account_count, unreconciled_account_count, is_complete,
            age_state, as_of, oldest_known_source_as_of
        ) VALUES (?, ?, ?, ?, 0, ?, ?, 0, 0, 0, 0, ?, ?, ?, ?)
        """,
        (
            run_id,
            _timestamp(taken_at),
            value_minor,
            value_minor,
            account_count,
            0 if is_complete else account_count,
            int(is_complete),
            "STATIC_ONLY" if as_of is None else "KNOWN",
            as_of,
            as_of,
        ),
    )


def _history_days(document: dict[str, Any]) -> list[tuple[str, int]]:
    return [(point["taken_at"], point["total"]["value_minor"]) for point in document["history"]]


def test_history_ends_on_the_published_snapshot_and_carries_what_the_phone_parses(
    db: sqlite3.Connection,
) -> None:
    _insert_snapshot(db, "run-earlier", NOW - timedelta(days=2), value_minor=9_000)
    _setup_snapshot(db)

    document = _document(_publisher(db).publish(at=NOW).envelope)

    assert _history_days(document) == [
        (_timestamp(NOW - timedelta(days=2)), 9_000),
        (_timestamp(NOW), 12_345),
    ]
    # The headline's own encoding, minus the fields no phone reads: a point and
    # the total above it cannot spell one stored fact two ways.
    last = document["history"][-1]["total"]
    assert last == {key: document["total"][key] for key in last}
    assert set(last) == {
        "account_count",
        "age_state",
        "as_of",
        "assets_minor",
        "currency",
        "is_complete",
        "liabilities_minor",
        "static_account_count",
        "unknown_freshness_account_count",
        "value_minor",
    }


def test_history_is_each_owner_day_latest_snapshot_across_both_dst_changes(
    db: sqlite3.Connection,
) -> None:
    # Instants chosen so UTC days, a fixed -08:00 and the real zone all disagree.
    # 2025-11-02 is the day DST ended (09:00Z); 2026-03-08 is the day it began
    # (10:00Z).
    readings = [
        ("2025-11-02T07:30:00Z", 1),  # Nov 2, 00:30 PDT
        ("2025-11-02T09:30:00Z", 2),  # Nov 2, 01:30 PST — same day, later
        ("2025-11-03T07:30:00Z", 3),  # Nov 2, 23:30 PST — UTC and -07:00 say Nov 3
        ("2025-11-03T08:30:00Z", 4),  # Nov 3, 00:30 PST
        ("2026-03-08T07:30:00Z", 5),  # Mar 7, 23:30 PST
        ("2026-03-09T06:30:00Z", 6),  # Mar 8, 23:30 PDT
        ("2026-03-09T07:30:00Z", 7),  # Mar 9, 00:30 PDT — a fixed -08:00 says Mar 8
    ]
    for index, (taken_at, value) in enumerate(readings):
        _insert_snapshot(db, f"run-{index}", datetime.fromisoformat(taken_at), value_minor=value)
    _setup_snapshot(db)

    document = _document(_publisher(db).publish(at=NOW).envelope)

    assert [value for _, value in _history_days(document)] == [3, 4, 5, 6, 7, 12_345]


def test_history_starts_with_the_first_snapshot_that_holds_an_account(
    db: sqlite3.Connection,
) -> None:
    # What every sync stores before the first Link: a total over nothing. On
    # the owner's host that was 85 consecutive $0.00 snapshots, and drawing them
    # would show his first real total as a cliff out of a month of zeros.
    _insert_snapshot(db, "run-empty-1", NOW - timedelta(days=3), value_minor=0, account_count=0)
    _insert_snapshot(db, "run-empty-2", NOW - timedelta(days=2), value_minor=0, account_count=0)
    _insert_snapshot(db, "run-first", NOW - timedelta(days=1), value_minor=8_000)
    _setup_snapshot(db)

    document = _document(_publisher(db).publish(at=NOW).envelope)

    assert [value for _, value in _history_days(document)] == [8_000, 12_345]


def test_an_incomplete_reading_stays_incomplete_in_the_series(
    db: sqlite3.Connection,
) -> None:
    _insert_snapshot(db, "run-partial", NOW - timedelta(days=1), is_complete=False)
    _setup_snapshot(db)

    history = _document(_publisher(db).publish(at=NOW).envelope)["history"]

    assert [point["total"]["is_complete"] for point in history] == [False, True]
    assert history[0]["total"]["age_state"] == "KNOWN"


def test_history_reaches_back_exactly_five_years_from_its_last_day(
    db: sqlite3.Connection,
) -> None:
    last_day = NOW.astimezone(OWNER_ZONE).date()
    boundary = last_day.replace(year=last_day.year - HISTORY_YEARS)
    for run_id, day, value in (
        ("run-outside", boundary - timedelta(days=1), 1),
        ("run-boundary", boundary, 2),
    ):
        # Noon in the owner's zone, so the day under test is unambiguous.
        taken_at = datetime(day.year, day.month, day.day, 12, tzinfo=OWNER_ZONE).astimezone(UTC)
        _insert_snapshot(db, run_id, taken_at, value_minor=value)
    _setup_snapshot(db)

    document = _document(_publisher(db).publish(at=NOW).envelope)

    assert [value for _, value in _history_days(document)] == [2, 12_345]


def test_five_full_years_of_history_fit_well_inside_what_the_phone_will_read(
    db: sqlite3.Connection,
) -> None:
    # `app/lib/src/data/snapshot_transport.dart::snapshotMaxBodyBytes`: the phone
    # stops reading a response at 1 MiB. A series that grew past it would not
    # degrade the curve, it would cost the owner his number.
    phone_read_cap = 1 << 20
    day = NOW - timedelta(days=1)
    for index in range(HISTORY_YEARS * 366 + 10):
        # Wide figures and incomplete readings, the most bytes a day can cost.
        _insert_snapshot(
            db,
            f"run-{index}",
            day - timedelta(days=index),
            value_minor=999_999_999_999,
            account_count=99,
            is_complete=False,
        )
    _setup_snapshot(db)

    publication = _publisher(db).publish(at=NOW)

    assert len(_document(publication.envelope)["history"]) <= HISTORY_YEARS * 366 + 1
    assert len(publication.envelope.to_json()) < phone_read_cap * 0.75
