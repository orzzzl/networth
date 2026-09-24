"""One unattended Sandbox Link worker pass; scheduling belongs to task 16."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing

from networth.link_lifecycle import run_lifecycle
from networth.plaid.client import PlaidClient
from networth.plaid.environment import (
    PlaidEnvironment,
    load_credentials,
    paths_for,
    selected_environment,
)
from networth.storage import migrate
from networth.tokenstore import TokenStore

SUMMARY = "Poll and reconcile recorded Sandbox Link requests without a completion prompt."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--flow", help="one recorded request UUID; omitted means all pending requests"
    )
    parser.add_argument(
        "--country-code", action="append", required=True, help="metadata country scope"
    )


def run(args: argparse.Namespace) -> int:
    try:
        environment = selected_environment()
        if environment is not PlaidEnvironment.SANDBOX:
            print(
                "poll-link currently permits Sandbox only; Production remains task 08.",
                file=sys.stderr,
            )
            return 2
        paths = paths_for(environment)
        client = PlaidClient(load_credentials(environment))
        store = TokenStore(paths.items)
        with closing(sqlite3.connect(paths.database.as_uri() + "?mode=rw", uri=True)) as db:
            migrate(db)
            flows = (
                [args.flow]
                if args.flow
                else [
                    row[0]
                    for row in db.execute(
                        "SELECT flow_id FROM link_request WHERE material_reaped_at IS NULL "
                        "OR EXISTS (SELECT 1 FROM link_result r "
                        "WHERE r.flow_id = link_request.flow_id "
                        "AND r.state = 'EXCHANGING') ORDER BY minted_at"
                    )
                ]
            )
            failed = False
            for flow in flows:
                try:
                    outcome = run_lifecycle(
                        db,
                        store,
                        client,
                        flow_id=flow,
                        country_codes=tuple(args.country_code),
                    )
                    failed |= outcome.worker.failed or outcome.worker.held
                    print(f"Link pass: {outcome}")
                except Exception:
                    failed = True
                    print(
                        "Link pass failed; retained evidence requires reinspection.",
                        file=sys.stderr,
                    )
            return 1 if failed else 0
    except Exception:
        print(
            "poll-link failed; verify Sandbox configuration and retained evidence.", file=sys.stderr
        )
        return 2
