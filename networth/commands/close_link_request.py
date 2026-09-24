"""Audited closure after the coverage observation has its reviewed slot count."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime

from networth.link_lifecycle import adjudicate_closure
from networth.plaid.environment import PlaidEnvironment, paths_for, selected_environment
from networth.storage import migrate
from networth.tokenstore import TokenStore

SUMMARY = "Close an expired Sandbox request after explicit coverage and child review."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--flow", required=True)
    parser.add_argument("--audit-id", required=True, help="UUID of private closure evidence")
    parser.add_argument("--confirm-reviewed", action="store_true")


def run(args: argparse.Namespace) -> int:
    try:
        env = selected_environment()
        if env is not PlaidEnvironment.SANDBOX:
            raise ValueError
        paths = paths_for(env)
        with closing(sqlite3.connect(paths.database.as_uri() + "?mode=rw", uri=True)) as db:
            migrate(db)
            closed, reaped = adjudicate_closure(
                db,
                TokenStore(paths.items),
                flow_id=args.flow,
                audit_id=args.audit_id,
                reviewed=args.confirm_reviewed,
                now=datetime.now(UTC),
            )
            if not closed:
                raise ValueError
        print(f"Request closure recorded; material reaped: {reaped}.")
        return 0
    except Exception:
        print(
            "Request closure refused; inspect coverage adjudication, poll and child evidence.",
            file=sys.stderr,
        )
        return 2
