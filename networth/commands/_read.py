"""Read-command plumbing: no migration, secret constructor or network client."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from networth.config import ConfigError
from networth.plaid.environment import paths_for, selected_environment
from networth.query import NetWorthQueryError
from networth.storage.migrations import require_current_schema
from networth.store import StoreError


@contextmanager
def database(path: Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA query_only = ON")
        db.execute("BEGIN")
        require_current_schema(db)
        yield db
    finally:
        db.close()


def _json(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    raise TypeError("unsupported report value")


def emit(report: object) -> None:
    print(json.dumps(report, default=_json, indent=2, sort_keys=True))


def execute(reader: Callable[[sqlite3.Connection, Path, datetime], tuple[object, int]]) -> int:
    try:
        paths = paths_for(selected_environment())
        with database(paths.database) as db:
            report, status = reader(db, paths.items, datetime.now(UTC))
        emit(report)
        return status
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
    except NetWorthQueryError:
        print(
            "Snapshot mismatch: no headline; a new successful snapshot is required. "
            "Run networth doctor for diagnostics.",
            file=sys.stderr,
        )
    except (OSError, sqlite3.Error, StoreError, ValueError, RuntimeError):
        # Never echo arbitrary database text or secret-path exception payloads.
        print(
            "Read failed: verify the selected database, schema and stored data. "
            "No repair or migration was attempted.",
            file=sys.stderr,
        )
    return 2
