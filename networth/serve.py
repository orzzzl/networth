"""The daemon's one HTTP route: ``GET /snapshot`` on the tailnet."""

from __future__ import annotations

import argparse
import ipaddress
import socket
import sqlite3
import sys
from collections.abc import Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import cast

from networth.config import ConfigError
from networth.listeners import (
    SERVE_PORT,
    ListenerCheckError,
    live_tailnet_addresses,
    select_bind_address,
)
from networth.payload import PayloadEnvelope, PayloadEnvelopeError
from networth.plaid.environment import paths_for, selected_environment


class SnapshotServeError(RuntimeError):
    """The configured database cannot produce the route's exact wire object."""


def open_read_only_database(path: Path) -> sqlite3.Connection:
    """Open the configured file with SQLite's write path disabled."""

    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.execute("PRAGMA query_only = ON")
    return connection


class SnapshotReader:
    """Reassemble the stored envelope without opening plaintext or a write handle."""

    __slots__ = ("_database",)

    def __init__(self, database: Path) -> None:
        if not isinstance(database, Path):
            raise TypeError("database must be a pathlib.Path")
        self._database = database

    def current(self) -> PayloadEnvelope | None:
        connection = open_read_only_database(self._database)
        try:
            row = connection.execute(
                """
                SELECT envelope.schema_version, envelope.pairing_id, envelope.seq,
                       envelope.published_at, envelope.nonce, envelope.ciphertext
                FROM published_envelope AS envelope
                JOIN pairing ON pairing.id = envelope.pairing_id
                WHERE envelope.is_active = 1
                  AND pairing.state = 'ACTIVE'
                  AND pairing.revoked_at IS NULL
                """
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        if not all(isinstance(value, str) for value in row[:4]):
            raise SnapshotServeError("stored envelope header is not text")
        if not isinstance(row[4], bytes) or not isinstance(row[5], bytes):
            raise SnapshotServeError("stored envelope body is not bytes")
        try:
            return PayloadEnvelope(
                schema_version=cast(str, row[0]),
                pairing_id=cast(str, row[1]),
                seq=cast(str, row[2]),
                published_at=cast(str, row[3]),
                nonce=row[4],
                payload=row[5],
            )
        except PayloadEnvelopeError as exc:
            raise SnapshotServeError("stored envelope violates the wire contract") from exc


class SnapshotHTTPServer(HTTPServer):
    """An HTTP server carrying only the read-only snapshot source."""

    def __init__(self, address: tuple[str, int], reader: SnapshotReader) -> None:
        self.reader = reader
        super().__init__(address, SnapshotRequestHandler)


class IPv6SnapshotHTTPServer(SnapshotHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


class SnapshotRequestHandler(BaseHTTPRequestHandler):
    """No endpoint exists here except the one design section 16 names."""

    protocol_version = "HTTP/1.1"

    def _empty(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
        if self.path != "/snapshot":
            self._empty(HTTPStatus.NOT_FOUND)
            return
        reader = cast(SnapshotHTTPServer, self.server).reader
        try:
            envelope = reader.current()
        except (OSError, sqlite3.Error, SnapshotServeError):
            # The client learns only that the source is unavailable.  Database
            # paths and stored values belong in local diagnostics, not a wire
            # response from the process that holds the owner's net worth.
            self._empty(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if envelope is None:
            self._empty(HTTPStatus.NOT_FOUND)
            return

        body = envelope.to_json()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _method_not_allowed(self) -> None:
        self._empty(HTTPStatus.METHOD_NOT_ALLOWED)

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
        self._method_not_allowed()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
        self._method_not_allowed()

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
        self._method_not_allowed()


def create_server(
    bind_address: str,
    port: int,
    reader: SnapshotReader,
) -> SnapshotHTTPServer:
    parsed = ipaddress.ip_address(bind_address)
    server_type = SnapshotHTTPServer if parsed.version == 4 else IPv6SnapshotHTTPServer
    return server_type((str(parsed), port), reader)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="networth-serve",
        description="Serve the active encrypted snapshot on this node's tailnet interface.",
    )
    parser.add_argument(
        "--bind",
        help=(
            "one current TailscaleIP; defaults to this node's IPv4 tailnet address "
            "and never falls back to wildcard or loopback"
        ),
    )
    parser.add_argument("--port", type=int, default=SERVE_PORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 < args.port <= 65_535:
        print("networth-serve: port must be between 1 and 65535", file=sys.stderr)
        return 2
    try:
        environment = selected_environment()
        database = paths_for(environment).database
        addresses = live_tailnet_addresses()
        bind_address = select_bind_address(addresses, args.bind)
        reader = SnapshotReader(database)
        reader.current()  # Fail before listening if the configured database is unreadable.
        server = create_server(bind_address, args.port, reader)
    except (ConfigError, ListenerCheckError, OSError, sqlite3.Error, SnapshotServeError) as exc:
        print(f"networth-serve: refusing to start: {exc}", file=sys.stderr)
        return 2

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


__all__ = [
    "IPv6SnapshotHTTPServer",
    "SnapshotHTTPServer",
    "SnapshotReader",
    "SnapshotRequestHandler",
    "SnapshotServeError",
    "build_parser",
    "create_server",
    "main",
    "open_read_only_database",
]
