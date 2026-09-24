"""Authenticated driver return leg; reads bearers only from the pipe."""

from __future__ import annotations

import argparse
import os
import sqlite3
import stat
import sys
from contextlib import closing

from networth.link_lifecycle import authorize_release
from networth.link_recovery import RecoveryRecord
from networth.plaid.environment import PlaidEnvironment, paths_for, selected_environment
from networth.storage import migrate
from networth.tokenstore import TokenStore

SUMMARY = "Commit automatic Link second-copy attestation and acknowledge URL release."
ACK = "networth-automatic-release-v1:"


def run(args: argparse.Namespace) -> int:
    try:
        env = selected_environment()
        if env is not PlaidEnvironment.SANDBOX or not stat.S_ISFIFO(
            os.fstat(sys.stdin.fileno()).st_mode
        ):
            raise ValueError
        record = RecoveryRecord.from_json(sys.stdin.read())
        paths = paths_for(env)
        with closing(sqlite3.connect(paths.database.as_uri() + "?mode=rw", uri=True)) as db:
            migrate(db)
            authorize_release(db, TokenStore(paths.items), record)
        print(ACK + record.flow_id)
        return 0
    except Exception:
        print(
            "Automatic release refused; retain the recovery record and withhold the URL.",
            file=sys.stderr,
        )
        return 2
