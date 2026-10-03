"""Render the query's total with its mandatory tagged age."""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from networth.commands._read import execute
from networth.query import NetWorthQuery
from networth.store import Store

SUMMARY = "Read the latest snapshot, tagged total age and account freshness as JSON."


def _read(db: sqlite3.Connection, tokens: Path, now: datetime) -> tuple[object, int]:
    value = NetWorthQuery(Store(db)).latest()
    if value is None:
        return {"status": "NO_SNAPSHOT", "action": "A successful snapshot is required."}, 1
    return {
        "read_at": now,
        "freshness_assessed_at": value.snapshot.taken_at,
        "freshness_scope": "Stored snapshot; not a live sync or a current freshness claim.",
        "latest": asdict(value),
    }, 0


def run(args: argparse.Namespace) -> int:
    return execute(_read)
