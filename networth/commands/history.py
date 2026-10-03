"""Immutable total history, or account history joined across its lineage."""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from networth.commands._read import execute
from networth.query import NetWorthQuery
from networth.store import Store

SUMMARY = "Read stored totals or one account lineage with source ages as JSON."


def _positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("account id must be a positive integer") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("account id must be a positive integer")
    return number


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--account", type=_positive, help="local account id; follows its lineage")


def run(args: argparse.Namespace) -> int:
    def read(db: sqlite3.Connection, tokens: Path, now: datetime) -> tuple[object, int]:
        query = NetWorthQuery(Store(db))
        values = query.history() if args.account is None else query.account_history(args.account)
        return {
            "read_at": now,
            "scope": "Stored history; ages belong to each entry.",
            "history": [asdict(value) for value in values],
        }, 0

    return execute(read)
