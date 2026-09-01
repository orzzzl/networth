"""Task 03: the migration runner and the complete section-7 schema."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

import pytest

from networth.storage import MigrationError, SchemaTooNewError, migrate

NOW = "2026-09-01T08:00:00Z"
LINK_STATES = (
    "URL_MINTED",
    "SESSION_STARTED",
    "SESSION_EXITED",
    "SUCCESS_PENDING_EXCHANGE",
    "EXCHANGING",
    "EXCHANGED",
    "EXCHANGE_UNCERTAIN",
    "URL_EXPIRED",
    "TOKEN_EXPIRED",
    "ABANDONED",
)


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def _columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})"))


def _migration_sql() -> str:
    return (
        resources.files("networth.storage.sql")
        .joinpath("0001_initial.sql")
        .read_text(encoding="utf-8")
    )


def _insert_sync_run(connection: sqlite3.Connection, run_id: str) -> None:
    connection.execute(
        'INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok) '
        "VALUES (?, ?, ?, 'TEST', 1)",
        (run_id, NOW, NOW),
    )


def _insert_snapshot(connection: sqlite3.Connection, run_id: str) -> int:
    cursor = connection.execute(
        """
        INSERT INTO snapshot(
            sync_run_id, taken_at,
            total_net_worth_minor, total_assets_minor, total_liabilities_minor,
            account_count, stale_account_count, unknown_freshness_account_count,
            static_account_count, reauth_account_count, unreconciled_account_count,
            is_complete, age_state, as_of, oldest_known_source_as_of
        ) VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 'STATIC_ONLY', NULL, NULL)
        """,
        (run_id, NOW),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _insert_manual_account(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        """
        INSERT INTO account(
            name, type, currency, sign, freshness_policy,
            include_in_net_worth, reconciliation_state, created_at
        ) VALUES ('Synthetic property', 'manual', 'USD', 1, 'MANUAL_STATIC', 1,
                  'CONFIRMED', ?)
        """,
        (NOW,),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _insert_link_flow(
    connection: sqlite3.Connection,
    *,
    flow_id: str,
    state: str,
    link_session_id: str | None = None,
    item_id: str | None = None,
    hosted_url_expires_at: str = "2026-09-01T08:30:00Z",
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO link_flow(
            flow_id, secret_ref, minted_at, hosted_url_expires_at, state,
            link_session_id, item_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            flow_id,
            f"link-flow/{flow_id}",
            NOW,
            hosted_url_expires_at,
            state,
            link_session_id,
            item_id,
        ),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def test_migrations_run_from_empty_and_are_idempotent() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        assert migrate(connection) == (1,)
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert connection.execute("PRAGMA busy_timeout").fetchone() == (5000,)
        before = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
        ).fetchall()

        assert migrate(connection) == ()

        after = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
        ).fetchall()
        assert after == before
    finally:
        connection.close()


def test_migration_persists_wal_mode_for_file_database(tmp_path: Path) -> None:
    database_path = tmp_path / "networth.db"
    connection = sqlite3.connect(database_path)
    try:
        assert migrate(connection) == (1,)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        connection.close()

    reopened = sqlite3.connect(database_path)
    try:
        assert reopened.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        reopened.close()


def test_migration_refuses_a_database_from_the_future() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA user_version = 2")
        with pytest.raises(SchemaTooNewError, match="newer than supported"):
            migrate(connection)
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
    finally:
        connection.close()


def test_migration_never_commits_a_callers_transaction() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE caller_work(value TEXT)")
        connection.execute("INSERT INTO caller_work VALUES ('uncommitted')")
        with pytest.raises(MigrationError, match="active transaction"):
            migrate(connection)
        connection.rollback()
        assert connection.execute("SELECT * FROM caller_work").fetchall() == []
    finally:
        connection.close()


def test_schema_has_exactly_the_required_tables_and_columns(db: sqlite3.Connection) -> None:
    expected = {
        "institution": ("id", "plaid_institution_id", "name", "is_oauth"),
        "item": (
            "id",
            "institution_id",
            "plaid_item_id",
            "secret_ref",
            "status",
            "status_since",
            "last_successful_sync",
            "last_attempted_sync",
            "last_error_code",
            "last_error_message",
            "consent_expiration_time",
            "replaces_item_id",
            "created_at",
        ),
        "account": (
            "id",
            "item_id",
            "plaid_account_id",
            "name",
            "official_name",
            "mask",
            "type",
            "subtype",
            "currency",
            "sign",
            "freshness_policy",
            "include_in_net_worth",
            "lineage_id",
            "reconciliation_state",
            "superseded_by_account_id",
            "superseded_at",
            "last_fetch_at",
            "last_source_as_of",
            "created_at",
            "archived_at",
        ),
        "manual_asset": (
            "account_id",
            "kind",
            "static_value_minor",
            "symbol",
            "share_count",
            "valued_as_of",
            "note",
        ),
        "sync_run": ("id", "started_at", "finished_at", "trigger", "ok", "error_summary"),
        "observation": (
            "id",
            "sync_run_id",
            "account_id",
            "observed_at",
            "value_minor",
            "currency",
            "source",
            "fetched_at",
            "source_as_of",
            "source_clock",
            "is_carried_forward",
        ),
        "snapshot": (
            "id",
            "sync_run_id",
            "taken_at",
            "total_net_worth_minor",
            "total_assets_minor",
            "total_liabilities_minor",
            "account_count",
            "stale_account_count",
            "unknown_freshness_account_count",
            "static_account_count",
            "reauth_account_count",
            "unreconciled_account_count",
            "is_complete",
            "age_state",
            "as_of",
            "oldest_known_source_as_of",
        ),
        "alert": (
            "id",
            "created_at",
            "kind",
            "item_id",
            "account_id",
            "message",
            "notified_at",
            "acknowledged_at",
            "resolved_at",
        ),
        "pairing": ("id", "created_at", "key_ref", "state", "revoked_at"),
        "publication": (
            "id",
            "snapshot_id",
            "pairing_id",
            "seq",
            "schema_version",
            "published_at",
            "ok",
            "error",
        ),
        "published_envelope": (
            "publication_id",
            "pairing_id",
            "schema_version",
            "seq",
            "published_at",
            "nonce",
            "ciphertext",
            "is_active",
        ),
        "backup_archive": (
            "id",
            "archive_id",
            "built_at",
            "archive_sha256",
            "byte_size",
            "manifest_sha256",
            "pulled_verified_at",
            "pulled_by",
            "verify_error",
        ),
        "backup_state": (
            "id",
            "key_escrow_confirmed_at",
            "last_verified_restore_at",
            "last_verified_restore_archive_id",
            "last_verified_restore_error",
        ),
        "daemon_state": ("id", "publish_epoch", "epoch_bumped_at", "epoch_bumped_reason"),
        "link_flow": (
            "id",
            "flow_id",
            "secret_ref",
            "minted_at",
            "hosted_url_expires_at",
            "started_at",
            "finished_at",
            "token_exchange_expires_at",
            "session_retention_expires_at",
            "second_copy_verified_at",
            "second_copy_holder",
            "state",
            "exchange_claimed_at",
            "exchange_claim_owner",
            "exchange_attempts",
            "last_poll_at",
            "poll_error",
            "link_session_id",
            "item_id",
            "material_reaped_at",
            "secret_ref_cleared_at",
        ),
        "link_exchange_attempt": ("link_flow_id", "attempt_number", "request_id"),
    }
    actual_tables = {
        str(row[0])
        for row in db.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert actual_tables == set(expected)
    for table, columns in expected.items():
        assert _columns(db, table) == columns, table

    assert all("profile_id" not in columns for columns in expected.values())
    assert "webhook_event" not in actual_tables
    assert {
        "db_row_counts_json",
        "item_count",
        "token_digest",
        "item_token_binding_sha256",
    }.isdisjoint(_columns(db, "backup_archive"))


def test_money_columns_are_integers_and_observations_keep_both_clocks(
    db: sqlite3.Connection,
) -> None:
    integer_money_columns = {
        ("manual_asset", "static_value_minor"),
        ("observation", "value_minor"),
        ("snapshot", "total_net_worth_minor"),
        ("snapshot", "total_assets_minor"),
        ("snapshot", "total_liabilities_minor"),
    }
    for table, column in integer_money_columns:
        types = {str(row[1]): str(row[2]) for row in db.execute(f"PRAGMA table_info({table})")}
        assert types[column] == "INTEGER"

    assert {"fetched_at", "source_as_of", "source_clock"}.issubset(_columns(db, "observation"))
    assert {"built_at", "pulled_verified_at"}.issubset(_columns(db, "backup_archive"))


def test_money_columns_reject_floats(db: sqlite3.Connection) -> None:
    account_id = _insert_manual_account(db)
    with pytest.raises(sqlite3.IntegrityError, match="cannot store REAL value"):
        db.execute(
            """
            INSERT INTO manual_asset(
                account_id, kind, static_value_minor, valued_as_of
            ) VALUES (?, 'REAL_PROPERTY', 1.5, ?)
            """,
            (account_id, NOW),
        )


@pytest.mark.parametrize("table", ["backup_state", "daemon_state"])
def test_singletons_are_seeded_and_reject_a_second_insert(
    db: sqlite3.Connection, table: str
) -> None:
    assert db.execute(f"SELECT id FROM {table}").fetchall() == [(1,)]
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        db.execute(f"INSERT INTO {table}(id) VALUES (1)")


def test_singleton_seed_writes_are_upserts() -> None:
    sql = _migration_sql()
    for table in ("backup_state", "daemon_state"):
        insert = re.search(
            rf"INSERT INTO {table}[(].*?;",
            sql,
            flags=re.DOTALL,
        )
        assert insert is not None
        assert re.search(
            r"ON CONFLICT[(]id[)]\s+DO UPDATE SET",
            insert.group(0),
            flags=re.DOTALL,
        )


def test_only_one_published_envelope_can_be_active(db: sqlite3.Connection) -> None:
    _insert_sync_run(db, "run-envelope")
    snapshot_id = _insert_snapshot(db, "run-envelope")
    db.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) "
        "VALUES ('pair-envelope', ?, 'payload-key/pair-envelope', 'ACTIVE')",
        (NOW,),
    )
    for seq in (1, 2, 3):
        db.execute(
            """
            INSERT INTO publication(
                id, snapshot_id, pairing_id, seq, schema_version, published_at, ok
            ) VALUES (?, ?, 'pair-envelope', ?, '1', ?, 1)
            """,
            (seq, snapshot_id, seq, NOW),
        )

    envelope = ("pair-envelope", "1", "1", NOW, b"n" * 12, b"c" * 16)
    db.execute(
        """
        INSERT INTO published_envelope(
            publication_id, pairing_id, schema_version, seq, published_at,
            nonce, ciphertext, is_active
        ) VALUES (1, ?, ?, ?, ?, ?, ?, 1)
        """,
        envelope,
    )
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        db.execute(
            """
            INSERT INTO published_envelope(
                publication_id, pairing_id, schema_version, seq, published_at,
                nonce, ciphertext, is_active
            ) VALUES (2, ?, ?, ?, ?, ?, ?, 1)
            """,
            envelope,
        )

    db.execute(
        """
        INSERT INTO published_envelope(
            publication_id, pairing_id, schema_version, seq, published_at,
            nonce, ciphertext, is_active
        ) VALUES (3, ?, ?, ?, ?, ?, ?, NULL)
        """,
        envelope,
    )


def test_pairing_rotation_can_activate_new_pairing_before_revoking_old(
    db: sqlite3.Connection,
) -> None:
    db.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) "
        "VALUES ('pair-old', ?, 'payload-key/pair-old', 'ACTIVE')",
        (NOW,),
    )
    db.commit()

    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(
            "INSERT INTO pairing(id, created_at, key_ref, state) "
            "VALUES ('pair-new', ?, 'payload-key/pair-new', 'ACTIVE')",
            (NOW,),
        )
        db.execute(
            "UPDATE pairing SET state = 'REVOKED', revoked_at = ? WHERE id = 'pair-old'",
            (NOW,),
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise

    assert db.execute("SELECT id, state FROM pairing ORDER BY id").fetchall() == [
        ("pair-new", "ACTIVE"),
        ("pair-old", "REVOKED"),
    ]


def test_publication_sequence_cannot_move_backwards(db: sqlite3.Connection) -> None:
    _insert_sync_run(db, "run-sequence")
    snapshot_id = _insert_snapshot(db, "run-sequence")
    db.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) "
        "VALUES ('pair-sequence', ?, 'payload-key/pair-sequence', 'ACTIVE')",
        (NOW,),
    )
    db.execute(
        """
        INSERT INTO publication(
            snapshot_id, pairing_id, seq, schema_version, published_at, ok
        ) VALUES (?, 'pair-sequence', 2, '1', ?, 1)
        """,
        (snapshot_id, NOW),
    )
    with pytest.raises(sqlite3.IntegrityError, match="must increase monotonically"):
        db.execute(
            """
            INSERT INTO publication(
                snapshot_id, pairing_id, seq, schema_version, published_at, ok
            ) VALUES (?, 'pair-sequence', 1, '1', ?, 1)
            """,
            (snapshot_id, NOW),
        )


@pytest.mark.parametrize("state", LINK_STATES)
def test_link_identifiers_are_independently_storable_in_every_state(
    db: sqlite3.Connection, state: str
) -> None:
    session_flow = f"{state.lower()}-session"
    item_flow = f"{state.lower()}-item"
    _insert_link_flow(
        db,
        flow_id=session_flow,
        state=state,
        link_session_id=f"session-{state.lower()}",
    )
    _insert_link_flow(
        db,
        flow_id=item_flow,
        state=state,
        item_id=f"item-{state.lower()}",
    )

    assert db.execute(
        "SELECT link_session_id, item_id FROM link_flow WHERE flow_id = ?", (session_flow,)
    ).fetchone() == (f"session-{state.lower()}", None)
    assert db.execute(
        "SELECT link_session_id, item_id FROM link_flow WHERE flow_id = ?", (item_flow,)
    ).fetchone() == (None, f"item-{state.lower()}")


def test_link_flows_can_record_repeat_activity_for_the_same_item(
    db: sqlite3.Connection,
) -> None:
    _insert_link_flow(
        db,
        flow_id="initial-link",
        state="EXCHANGED",
        item_id="reused-item",
    )
    _insert_link_flow(
        db,
        flow_id="update-mode-link",
        state="EXCHANGED",
        item_id="reused-item",
    )

    assert db.execute(
        "SELECT flow_id FROM link_flow WHERE item_id = ? ORDER BY id",
        ("reused-item",),
    ).fetchall() == [("initial-link",), ("update-mode-link",)]


def test_only_completed_link_failures_are_counted_as_stranded(db: sqlite3.Connection) -> None:
    _insert_link_flow(
        db,
        flow_id="expired-url-still-minted",
        state="URL_MINTED",
        hosted_url_expires_at="2026-08-31T08:00:00Z",
    )
    _insert_link_flow(db, flow_id="url-expired", state="URL_EXPIRED")
    _insert_link_flow(db, flow_id="token-expired", state="TOKEN_EXPIRED")
    _insert_link_flow(db, flow_id="exchange-uncertain", state="EXCHANGE_UNCERTAIN")

    stranded = db.execute("SELECT flow_id FROM stranded_link_flow ORDER BY flow_id").fetchall()
    assert stranded == [("exchange-uncertain",), ("token-expired",)]


def test_exchange_attempts_store_request_id_per_attempt_and_nothing_token_shaped(
    db: sqlite3.Connection,
) -> None:
    flow_id = _insert_link_flow(db, flow_id="attempts", state="EXCHANGE_UNCERTAIN")
    db.executemany(
        "INSERT INTO link_exchange_attempt(link_flow_id, attempt_number, request_id) "
        "VALUES (?, ?, ?)",
        [(flow_id, 1, "request-one"), (flow_id, 2, "request-two")],
    )
    assert db.execute(
        "SELECT attempt_number, request_id FROM link_exchange_attempt ORDER BY attempt_number"
    ).fetchall() == [(1, "request-one"), (2, "request-two")]

    columns = _columns(db, "link_exchange_attempt")
    assert columns == ("link_flow_id", "attempt_number", "request_id")
    assert all("token" not in column.lower() for column in columns)

    table_sql = db.execute(
        "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = 'link_exchange_attempt'"
    ).fetchone()
    assert table_sql is not None
    assert "token" not in str(table_sql[0]).lower()

    source_block = re.search(
        r"CREATE TABLE link_exchange_attempt [(](.*?)[)] STRICT;",
        _migration_sql(),
        flags=re.DOTALL,
    )
    assert source_block is not None
    assert "token" not in source_block.group(1).lower()


def test_link_cleanup_crash_states_are_separately_representable(db: sqlite3.Connection) -> None:
    flow_id = _insert_link_flow(db, flow_id="cleanup", state="ABANDONED")
    db.execute(
        "UPDATE link_flow SET material_reaped_at = ? WHERE id = ?",
        (NOW, flow_id),
    )
    assert db.execute(
        "SELECT secret_ref, material_reaped_at, secret_ref_cleared_at FROM link_flow WHERE id = ?",
        (flow_id,),
    ).fetchone() == ("link-flow/cleanup", NOW, None)

    db.execute(
        "UPDATE link_flow SET secret_ref = NULL, secret_ref_cleared_at = ? WHERE id = ?",
        (NOW, flow_id),
    )
    assert db.execute(
        "SELECT secret_ref, material_reaped_at, secret_ref_cleared_at FROM link_flow WHERE id = ?",
        (flow_id,),
    ).fetchone() == (None, NOW, NOW)


def test_account_lineage_defaults_to_the_accounts_own_id(db: sqlite3.Connection) -> None:
    account_id = _insert_manual_account(db)
    assert db.execute("SELECT lineage_id FROM account WHERE id = ?", (account_id,)).fetchone() == (
        account_id,
    )


def test_source_clock_cannot_claim_a_date_when_it_is_unknown(db: sqlite3.Connection) -> None:
    account_id = _insert_manual_account(db)
    _insert_sync_run(db, "run-observation")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        db.execute(
            """
            INSERT INTO observation(
                sync_run_id, account_id, observed_at, value_minor, currency,
                source, fetched_at, source_as_of, source_clock, is_carried_forward
            ) VALUES ('run-observation', ?, ?, 100, 'USD', 'MANUAL', ?, ?, 'UNKNOWN', 0)
            """,
            (account_id, NOW, NOW, NOW),
        )


def test_snapshot_age_is_a_tagged_value_and_sync_run_is_unique(db: sqlite3.Connection) -> None:
    _insert_sync_run(db, "run-snapshot")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        db.execute(
            """
            INSERT INTO snapshot(
                sync_run_id, taken_at,
                total_net_worth_minor, total_assets_minor, total_liabilities_minor,
                account_count, stale_account_count, unknown_freshness_account_count,
                static_account_count, reauth_account_count, unreconciled_account_count,
                is_complete, age_state, as_of
            ) VALUES ('run-snapshot', ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1,
                      'UNKNOWN', ?)
            """,
            (NOW, NOW),
        )

    _insert_snapshot(db, "run-snapshot")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        _insert_snapshot(db, "run-snapshot")
