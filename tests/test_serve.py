"""Task 20: one read-only route serving the exact stored envelope."""

from __future__ import annotations

import http.client
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from networth.payload import PayloadEnvelope
from networth.serve import SnapshotHTTPServer, SnapshotReader, open_read_only_database
from networth.storage import migrate

STAMP = "2026-09-16T08:00:00.000000Z"
PAIRING_ID = "00000000-0000-4000-8000-000000000020"
ENVELOPE = PayloadEnvelope(
    schema_version="1",
    pairing_id=PAIRING_ID,
    seq="7",
    published_at=STAMP,
    nonce=b"n" * 12,
    payload=b"ciphertext" + b"t" * 16,
)


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        migrate(connection)
        connection.execute(
            'INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok) '
            "VALUES ('run-serve', ?, ?, 'TEST', 1)",
            (STAMP, STAMP),
        )
        connection.execute(
            """
            INSERT INTO snapshot(
                sync_run_id, taken_at, total_net_worth_minor, total_assets_minor,
                total_liabilities_minor, account_count, stale_account_count,
                unknown_freshness_account_count, static_account_count,
                reauth_account_count, unreconciled_account_count, is_complete,
                age_state, as_of, oldest_known_source_as_of
            ) VALUES ('run-serve', ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1,
                      'STATIC_ONLY', NULL, NULL)
            """,
            (STAMP,),
        )
        connection.execute(
            "INSERT INTO pairing(id, created_at, key_ref, state) "
            "VALUES (?, ?, 'payload-key/serve', 'ACTIVE')",
            (PAIRING_ID, STAMP),
        )
        publication = connection.execute(
            """
            INSERT INTO publication(
                snapshot_id, pairing_id, seq, schema_version, published_at
            ) VALUES (1, ?, 7, '1', ?)
            """,
            (PAIRING_ID, STAMP),
        )
        assert publication.lastrowid is not None
        connection.execute(
            """
            INSERT INTO published_envelope(
                publication_id, pairing_id, schema_version, seq, published_at,
                nonce, ciphertext, is_active
            ) VALUES (?, ?, '1', '7', ?, ?, ?, 1)
            """,
            (
                int(publication.lastrowid),
                PAIRING_ID,
                STAMP,
                ENVELOPE.nonce,
                ENVELOPE.payload,
            ),
        )
        connection.commit()
    finally:
        connection.close()


@contextmanager
def _running_server(database: Path) -> Iterator[tuple[str, int]]:
    server = SnapshotHTTPServer(("127.0.0.1", 0), SnapshotReader(database))
    address = server.server_address
    host, port = str(address[0]), int(address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _request(address: tuple[str, int], method: str, path: str) -> tuple[int, bytes, dict[str, str]]:
    connection = http.client.HTTPConnection(*address, timeout=5)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        return response.status, response.read(), headers
    finally:
        connection.close()


def test_snapshot_route_reassembles_the_exact_stored_envelope_without_reencrypting(
    tmp_path: Path,
) -> None:
    database = tmp_path / "networth.db"
    _database(database)

    with _running_server(database) as address:
        status, body, headers = _request(address, "GET", "/snapshot")

    assert status == 200
    assert body == ENVELOPE.to_json()
    assert headers["content-type"] == "application/json"
    assert headers["cache-control"] == "no-store"


def test_revoked_pairing_turns_the_same_stored_envelope_into_404(tmp_path: Path) -> None:
    database = tmp_path / "networth.db"
    _database(database)

    with _running_server(database) as address:
        assert _request(address, "GET", "/snapshot")[0] == 200
        writer = sqlite3.connect(database)
        try:
            writer.execute(
                "UPDATE pairing SET state = 'REVOKED', revoked_at = ? WHERE id = ?",
                (STAMP, PAIRING_ID),
            )
            writer.commit()
        finally:
            writer.close()
        status, body, _headers = _request(address, "GET", "/snapshot")

    assert status == 404
    assert body == b""


def test_no_other_route_or_write_method_exists(tmp_path: Path) -> None:
    database = tmp_path / "networth.db"
    _database(database)

    with _running_server(database) as address:
        missing = _request(address, "GET", "/anything-else")
        write = _request(address, "POST", "/snapshot")

    assert missing[:2] == (404, b"")
    assert write[:2] == (405, b"")


def test_serving_connection_is_read_only_by_open_mode_and_query_guard(tmp_path: Path) -> None:
    database = tmp_path / "networth.db"
    _database(database)

    connection = open_read_only_database(database)
    try:
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM published_envelope")
    finally:
        connection.close()
