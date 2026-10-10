"""``networth link-authorize`` — the return leg that lets the Mac show the URL.

The owner's driver has written and read back its second copy of the recovery
record; this verb is how the host learns that, and its acknowledgement is the
only thing that permits the URL to be displayed. The commit is
:func:`networth.link_lifecycle.authorize_release`, unchanged: SSH authenticates
the caller, possession of the ``link_token`` binds the return to the request,
and the request must still be unreleased-or-released ``URL_MINTED`` with an
unexpired URL.

The record arrives on stdin and only on stdin. It carries the ``link_token``
and the URL, so it is never an argument (visible in the process table) and
never read from a terminal.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import stat
import sys
from contextlib import closing

from networth.commands.authorize_automatic_link import ACK
from networth.commands.link_mint import (
    Refusal,
    add_arguments,
    describe_failure,
    require_stated_environment,
)
from networth.link_lifecycle import authorize_release
from networth.link_recovery import RecoveryRecord
from networth.plaid.environment import paths_for
from networth.storage import migrate
from networth.tokenstore import TokenStore

SUMMARY = "Record the Mac's second-copy attestation and acknowledge URL release."

__all__ = ["ACK", "SUMMARY", "add_arguments", "run"]


def run(args: argparse.Namespace) -> int:
    try:
        environment = require_stated_environment(args.environment)
        if not stat.S_ISFIFO(os.fstat(sys.stdin.fileno()).st_mode):
            raise Refusal(
                "the recovery record is read from a pipe and stdin is not one; "
                "nothing was authorised"
            )
        record = RecoveryRecord.from_json(sys.stdin.read())
        paths = paths_for(environment)
        with closing(sqlite3.connect(paths.database.as_uri() + "?mode=rw", uri=True)) as db:
            migrate(db)
            authorize_release(db, TokenStore(paths.items), record)
    except Exception as exc:
        print(
            f"link-authorize stopped: {describe_failure(exc)}. Keep the recovery record "
            "and show no URL.",
            file=sys.stderr,
        )
        return 2
    print(ACK + record.flow_id)
    return 0
