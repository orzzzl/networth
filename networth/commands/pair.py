"""Provision or rotate the phone's payload key (task 19a)."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from networth.config import SECRETS_DIR, ConfigError
from networth.pairing import (
    PAYLOAD_KEY_FILENAME,
    PairingError,
    PairingStore,
    StagedPayloadKey,
    discover_tailnet_name,
    new_provision,
    payload_key_ref,
)
from networth.plaid.environment import paths_for, selected_environment
from networth.storage import MigrationError, migrate
from networth.terminal_qr import QrEncodingError, render_terminal_qr

SUMMARY = "Pair a phone or rotate the current phone's payload key."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--payload-key-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--tailnet-name",
        help="the VPS's full tailnet DNS name (normally read from tailscale status)",
    )


def _database(args: argparse.Namespace) -> Path:
    supplied = getattr(args, "database", None)
    if isinstance(supplied, Path):
        return supplied
    return paths_for(selected_environment()).database


def _key_file(args: argparse.Namespace) -> Path:
    supplied = getattr(args, "payload_key_file", None)
    return supplied if isinstance(supplied, Path) else SECRETS_DIR / PAYLOAD_KEY_FILENAME


def run(args: argparse.Namespace) -> int:
    try:
        tailnet_name = args.tailnet_name or discover_tailnet_name()
        provision = new_provision(tailnet_name)
        database = _database(args)
        key_file = _key_file(args)
        database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(database, timeout=5.0)) as connection:
            migrate(connection)
            with StagedPayloadKey(key_file, provision.payload_key) as staged:
                PairingStore(connection).rotate(
                    provision,
                    key_ref=payload_key_ref(provision.pairing_id),
                    at=datetime.now(UTC),
                    before_commit=staged.install,
                )
                staged.committed()
        encoded = provision.encode()
        qr = render_terminal_qr(encoded, ansi=sys.stdout.isatty())
    except (
        ConfigError,
        MigrationError,
        PairingError,
        QrEncodingError,
        OSError,
        sqlite3.Error,
    ) as exc:
        print(f"pairing failed: {exc}", file=sys.stderr)
        return 2

    print("Pairing committed. Scan this code in the networth app:")
    print(qr)
    print("Typed fallback (secret; enter it only in the networth app):")
    print(encoded)
    print(
        "The previous phone cannot fetch again. Ciphertext already cached on a lost or "
        "stolen phone is beyond recall; rotation stops future fetches, never past ones."
    )
    return 0
