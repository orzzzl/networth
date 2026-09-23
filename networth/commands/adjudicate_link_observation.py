"""Explicit local adjudication of otherwise uncountable successful Link evidence."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime

from networth.config import ConfigError
from networth.link_observations import ObservationError, adjudicate_observation
from networth.plaid.environment import paths_for, selected_environment
from networth.storage import MigrationError, migrate
from networth.tokenstore import TokenStoreError

SUMMARY = "Record a reviewed additional-slot count for a Link success observation."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--observation", required=True, help="the unresolved observation UUID")
    parser.add_argument(
        "--additional-slots",
        type=int,
        required=True,
        help="spent slots beyond all Items/results and other adjudications",
    )
    parser.add_argument(
        "--audit-id", required=True, help="UUID identifying the private review record"
    )
    parser.add_argument(
        "--confirm-reviewed",
        action="store_true",
        help="polling is quiesced and the complete evidence set was reviewed",
    )


def run(args: argparse.Namespace) -> int:
    try:
        if not args.confirm_reviewed:
            raise ObservationError("quiesce polling and confirm the reviewed slot outcome")
        database = paths_for(selected_environment()).database
        # Never create a new database from a misspelled path or absent deployment.
        with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True)) as connection:
            migrate(connection)
            adjudicate_observation(
                connection,
                observation_id=args.observation,
                additional_slots=args.additional_slots,
                audit_id=args.audit_id,
                now=datetime.now(UTC),
                reviewed=args.confirm_reviewed,
            )
    except (ConfigError, ObservationError, MigrationError, TokenStoreError, OSError, sqlite3.Error):
        print(
            "Adjudication refused; verify environment, unresolved observation, "
            "review and arguments.",
            file=sys.stderr,
        )
        return 2
    print(
        "Adjudication recorded. Other holds remain; later success evidence requires a new review."
    )
    return 0
