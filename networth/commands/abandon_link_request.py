"""Record abandon intent; a later lifecycle pass decides closure and cleanup."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime

from networth.link_lifecycle import request_abandon
from networth.plaid.environment import PlaidEnvironment, paths_for, selected_environment
from networth.storage import migrate

SUMMARY = "Record Sandbox request abandon intent without revoking or deleting its URL."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--flow", required=True)


def run(args: argparse.Namespace) -> int:
    try:
        env = selected_environment()
        if env is not PlaidEnvironment.SANDBOX:
            raise ValueError
        with closing(
            sqlite3.connect(paths_for(env).database.as_uri() + "?mode=rw", uri=True)
        ) as db:
            migrate(db)
            request_abandon(db, flow_id=args.flow, now=datetime.now(UTC))
        print("Abandon intent recorded; polling and retention remain governed by request evidence.")
        return 0
    except Exception:
        print("Request abandon refused; verify recorded Sandbox request.", file=sys.stderr)
        return 2
