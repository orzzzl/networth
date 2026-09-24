"""Automatic Sandbox mint over an authenticated pipe; measurement verbs stay separate."""

from __future__ import annotations

import argparse
import os
import sqlite3
import stat
import sys
from contextlib import closing

from networth.link_lifecycle import mint_request
from networth.plaid.client import PlaidClient
from networth.plaid.environment import (
    PlaidEnvironment,
    load_credentials,
    paths_for,
    selected_environment,
)
from networth.plaid.rehearsal import COUNTRY_CODES
from networth.storage import migrate
from networth.tokenstore import TokenStore

SUMMARY = "Mint a durable automatic Sandbox request for the verified holder driver."


def run(args: argparse.Namespace) -> int:
    try:
        env = selected_environment()
        if env is not PlaidEnvironment.SANDBOX or not stat.S_ISFIFO(
            os.fstat(sys.stdout.fileno()).st_mode
        ):
            raise ValueError
        paths = paths_for(env)
        client = PlaidClient(load_credentials(env))
        with closing(sqlite3.connect(paths.database.as_uri() + "?mode=rw", uri=True)) as db:
            migrate(db)
            result = mint_request(db, TokenStore(paths.items), client, country_codes=COUNTRY_CODES)
        print(result.to_wire())
        return 0
    except Exception:
        print(
            "Automatic mint refused or interrupted; inspect retained request before retrying.",
            file=sys.stderr,
        )
        return 2
