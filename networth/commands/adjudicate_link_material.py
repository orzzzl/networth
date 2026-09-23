"""Explicit local adjudication of persisted Link credential safety holds."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime

from networth.config import ConfigError
from networth.link_reconciliation import ReconciliationError, adjudicate_material_hold
from networth.plaid.environment import paths_for, selected_environment
from networth.storage import MigrationError, migrate
from networth.tokenstore import TokenStoreError

SUMMARY = "Permit reinspection of a reviewed Link material hold."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--hold", required=True, help="the unresolved material hold UUID")
    parser.add_argument(
        "--audit-id", required=True, help="UUID identifying the private review record"
    )
    parser.add_argument(
        "--confirm-reviewed",
        action="store_true",
        help="workers are quiesced; attribution and all possible spent slots were reviewed",
    )


def run(args: argparse.Namespace) -> int:
    try:
        if not args.confirm_reviewed:
            raise ReconciliationError("quiesce workers and confirm the reviewed material outcome")
        database = paths_for(selected_environment()).database
        # Never create a new database from a misspelled path or absent deployment.
        with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True)) as connection:
            migrate(connection)
            adjudicate_material_hold(
                connection,
                hold_id=args.hold,
                audit_id=args.audit_id,
                now=datetime.now(UTC),
                reviewed=args.confirm_reviewed,
            )
    except (
        ConfigError,
        ReconciliationError,
        MigrationError,
        TokenStoreError,
        OSError,
        sqlite3.Error,
    ):
        print(
            "Adjudication refused; verify environment, unresolved material hold, "
            "review and arguments.",
            file=sys.stderr,
        )
        return 2
    print(
        "Material review recorded. Reinspection is required; other holds and result states remain."
    )
    return 0
