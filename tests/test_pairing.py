"""Task 19a: pairing rotation and revocation are local, atomic DB changes."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from networth.pairing import (
    PairingPayloadError,
    PairingProvision,
    PairingStore,
    PairingTransactionError,
    StagedPayloadKey,
    decode_provision,
    new_provision,
    read_payload_key,
)
from networth.storage import migrate
from networth.terminal_qr import encode_qr, render_terminal_qr

NOW = datetime(2026, 9, 16, 22, 0, tzinfo=UTC)
OLD_PAIRING = "00000000-0000-4000-8000-000000000001"
NEW_PAIRING = "00000000-0000-4000-8000-000000000002"
KEY = bytes(range(32))
TAILNET_NAME = "vps.synthetic-tailnet.ts.net"


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    yield connection
    connection.close()


def _provision() -> PairingProvision:
    return PairingProvision(NEW_PAIRING, KEY, TAILNET_NAME)


def _seed_served_pairing(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) VALUES (?, ?, ?, 'ACTIVE')",
        (OLD_PAIRING, "2026-09-15T22:00:00Z", "/synthetic/old.key"),
    )
    connection.execute(
        """
        INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok)
        VALUES ('run-pairing', '2026-09-15T21:59:00Z', '2026-09-15T22:00:00Z', 'timer', 1)
        """
    )
    connection.execute(
        """
        INSERT INTO snapshot(
            sync_run_id, taken_at, total_net_worth_minor, total_assets_minor,
            total_liabilities_minor, account_count, stale_account_count,
            unknown_freshness_account_count, static_account_count,
            reauth_account_count, unreconciled_account_count, is_complete,
            age_state, as_of, oldest_known_source_as_of
        ) VALUES (
            'run-pairing', '2026-09-15T22:00:00Z', 10000, 10000,
            0, 1, 0, 0, 0, 0, 0, 1,
            'KNOWN', '2026-09-15T21:00:00Z', '2026-09-15T21:00:00Z'
        )
        """
    )
    snapshot_id = connection.execute(
        "SELECT id FROM snapshot WHERE sync_run_id = 'run-pairing'"
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO publication(snapshot_id, pairing_id, seq, schema_version, published_at)
        VALUES (?, ?, 1, '1', '2026-09-15T22:00:00Z')
        """,
        (snapshot_id, OLD_PAIRING),
    )
    connection.execute(
        """
        INSERT INTO published_envelope(
            publication_id, pairing_id, schema_version, seq, published_at,
            nonce, ciphertext, is_active
        ) VALUES (1, ?, '1', '1', '2026-09-15T22:00:00Z', ?, ?, 1)
        """,
        (OLD_PAIRING, b"n" * 12, b"c" * 16),
    )
    connection.commit()


def test_pairing_payload_round_trips_exactly_three_provisioning_values() -> None:
    provision = _provision()
    encoded = provision.encode()

    assert decode_provision(encoded) == provision
    assert encoded.split(":") == [
        "networth-pairing",
        "v1",
        NEW_PAIRING,
        "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        TAILNET_NAME,
    ]
    assert "token" not in encoded.lower()
    assert "read" not in encoded.lower()
    assert "<redacted>" in repr(provision)
    assert provision.payload_key.hex() not in repr(provision)


@pytest.mark.parametrize(
    "changed",
    [
        "networth-pairing:v2:" + NEW_PAIRING + ":key:" + TAILNET_NAME,
        "networth-pairing:v1:" + NEW_PAIRING + ":padded==:" + TAILNET_NAME,
        "networth-pairing:v1:" + OLD_PAIRING.replace("-4", "-3", 1) + ":key:" + TAILNET_NAME,
        "networth-pairing:v1:" + NEW_PAIRING + ":key:host-prefix",
        "networth-pairing:v1:" + NEW_PAIRING + ":key:" + TAILNET_NAME + ":read-token",
    ],
)
def test_pairing_payload_refuses_wrong_version_shape_key_id_or_host(changed: str) -> None:
    with pytest.raises(PairingPayloadError):
        decode_provision(changed)


def test_new_provision_requires_uuid4_and_exactly_32_random_bytes() -> None:
    provision = new_provision(
        TAILNET_NAME,
        key_factory=lambda size: b"k" * size,
        id_factory=lambda: uuid.UUID(NEW_PAIRING),
    )
    assert provision.payload_key == b"k" * 32

    with pytest.raises(PairingPayloadError, match="UUIDv4"):
        new_provision(
            TAILNET_NAME,
            key_factory=lambda size: b"k" * size,
            id_factory=lambda: uuid.UUID("00000000-0000-1000-8000-000000000002"),
        )


def test_rotation_activates_new_revokes_old_and_drops_envelope_in_one_transaction(
    db: sqlite3.Connection,
) -> None:
    _seed_served_pairing(db)
    observed_inside: list[tuple[list[tuple[object, ...]], int]] = []

    def before_commit() -> None:
        rows = db.execute("SELECT id, state, revoked_at FROM pairing ORDER BY id").fetchall()
        envelope_count = db.execute("SELECT count(*) FROM published_envelope").fetchone()[0]
        observed_inside.append((rows, envelope_count))

    PairingStore(db).rotate(
        _provision(), key_ref="/synthetic/new.key", at=NOW, before_commit=before_commit
    )

    expected = [
        (OLD_PAIRING, "REVOKED", "2026-09-16T22:00:00.000000Z"),
        (NEW_PAIRING, "ACTIVE", None),
    ]
    assert observed_inside == [(expected, 0)]
    assert (
        db.execute("SELECT id, state, revoked_at FROM pairing ORDER BY id").fetchall() == expected
    )
    assert db.execute("SELECT count(*) FROM published_envelope").fetchone() == (0,)


def test_rotation_rolls_every_database_change_back_if_precommit_work_fails(
    db: sqlite3.Connection,
) -> None:
    _seed_served_pairing(db)

    def refuse() -> None:
        raise OSError("synthetic key-file refusal")

    with pytest.raises(OSError, match="synthetic key-file refusal"):
        PairingStore(db).rotate(
            _provision(), key_ref="/synthetic/new.key", at=NOW, before_commit=refuse
        )

    assert db.execute("SELECT id, state, revoked_at FROM pairing").fetchall() == [
        (OLD_PAIRING, "ACTIVE", None)
    ]
    assert db.execute("SELECT count(*) FROM published_envelope").fetchone() == (1,)


def test_revoke_rolls_pairing_and_envelope_back_together_on_delete_failure(
    db: sqlite3.Connection,
) -> None:
    _seed_served_pairing(db)
    db.execute(
        """
        CREATE TEMP TRIGGER refuse_envelope_delete
        BEFORE DELETE ON published_envelope
        BEGIN
            SELECT raise(ABORT, 'synthetic envelope refusal');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic envelope refusal"):
        PairingStore(db).revoke(at=NOW)

    assert db.execute("SELECT state, revoked_at FROM pairing").fetchone() == ("ACTIVE", None)
    assert db.execute("SELECT count(*) FROM published_envelope").fetchone() == (1,)


def test_revoke_commits_no_active_pairing_and_no_served_envelope(db: sqlite3.Connection) -> None:
    _seed_served_pairing(db)
    assert PairingStore(db).revoke(at=NOW) == 1
    assert db.execute("SELECT state, revoked_at FROM pairing").fetchone() == (
        "REVOKED",
        "2026-09-16T22:00:00.000000Z",
    )
    assert db.execute("SELECT count(*) FROM published_envelope").fetchone() == (0,)


def test_pairing_store_refuses_to_hide_inside_a_callers_transaction(db: sqlite3.Connection) -> None:
    db.execute("BEGIN")
    with pytest.raises(PairingTransactionError, match="no active transaction"):
        PairingStore(db).revoke(at=NOW)
    db.rollback()


def test_staged_key_is_mode_600_and_restores_the_previous_key_on_failure(tmp_path: Path) -> None:
    path = tmp_path / "networth-payload.key"
    old = bytes(reversed(range(32)))
    with StagedPayloadKey(path, old) as staged:
        staged.install()
        staged.committed()
    assert read_payload_key(path) == old
    assert path.stat().st_mode & 0o777 == 0o600

    with (
        pytest.raises(RuntimeError, match="synthetic database failure"),
        StagedPayloadKey(path, KEY) as staged,
    ):
        staged.install()
        raise RuntimeError("synthetic database failure")
    assert read_payload_key(path) == old
    assert not tuple(tmp_path.glob(".networth-payload.key.*"))


def test_terminal_qr_has_the_version_10_size_and_a_four_module_quiet_zone() -> None:
    encoded = _provision().encode()
    matrix = encode_qr(encoded)
    assert len(matrix) == 57
    assert all(len(row) == 57 for row in matrix)
    assert matrix[0][:7] == (True, True, True, True, True, True, True)

    rendered = render_terminal_qr(encoded)
    lines = rendered.splitlines()
    assert len(lines) == 33
    assert all(len(line) == 65 for line in lines)
    assert lines[0].strip() == ""
