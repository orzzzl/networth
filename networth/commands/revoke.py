"""Revoke phone access without provisioning a replacement (task 19a)."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from networth.config import ConfigError
from networth.pairing import PairingError, PairingStore
from networth.plaid.environment import paths_for, selected_environment
from networth.storage import MigrationError, migrate

SUMMARY = "Revoke the current phone and immediately stop future snapshot fetches."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", type=Path, help=argparse.SUPPRESS)


def _database(args: argparse.Namespace) -> Path:
    supplied = getattr(args, "database", None)
    if isinstance(supplied, Path):
        return supplied
    return paths_for(selected_environment()).database


def run(args: argparse.Namespace) -> int:
    try:
        database = _database(args)
        with closing(sqlite3.connect(database, timeout=5.0)) as connection:
            migrate(connection)
            revoked = PairingStore(connection).revoke(at=datetime.now(UTC))
    except (ConfigError, MigrationError, PairingError, OSError, sqlite3.Error) as exc:
        print(f"revocation failed: {exc}", file=sys.stderr)
        return 2

    noun = "pairing" if revoked == 1 else "pairings"
    print(f"Revoked {revoked} active {noun}; the served envelope was dropped in the same commit.")
    print(
        "Future fetches are stopped. Ciphertext already cached on a lost or stolen phone is "
        "beyond recall; revocation does not reach backwards."
    )
    print("Also remove the lost device in the Tailscale admin console to revoke reachability.")
    return 0
