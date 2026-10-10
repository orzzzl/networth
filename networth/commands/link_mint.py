"""``networth link-mint`` — the host half of the owner's Link, in either environment.

Every other mint on ``main`` refuses anything but Sandbox, and each refusal says
the same thing: *Production Link is task 08, run by the owner*. This is that
verb. It adds no minting logic — :func:`networth.link_lifecycle.mint_request` is
environment-agnostic and already does the budget check, the durable
``link_token`` and the ``link_request`` row — so what lives here is only what
makes it safe to call against Production.

**The caller states the environment, and a disagreement is a refusal.**
``NETWORTH_ENV`` selects the credential file, the token store and the database
together (§15), and it reaches this process from whatever started it. The driver
on the Mac knows which environment the owner asked for; this host knows which
one it was started in. A rehearsal that was meant for Sandbox and arrived in a
Production environment is the one accident a lifetime slot cannot absorb, so
the two are compared before anything is read or minted.

**The mint payload leaves on a pipe or not at all.** It carries a ``link_token``
and an openable URL. The URL may be shown only by the Mac, after its second copy
verifies (§4); a terminal would show it here, before that copy exists.

**The deploy lock is held from before the provider call through the row.** A
runtime upgrade checks for pending requests and then switches code; a mint that
slipped between those two steps would persist a request the new code never
agreed to complete. ``scripts/install-runtime.sh`` holds the same file
exclusively for its whole run. Taken non-blocking: a mint that cannot have the
lock mints nothing, which is free (**F2a**). It is released before this verb
returns, so the owner's time in the browser never holds it.

**A failure after the request is durable is not permission to mint again
immediately** — ``mint_request`` says so. No URL was shown for that request, so
nothing can be spent through it; it expires on Plaid's clock and the lifecycle
worker closes it.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import stat
import sys
from contextlib import closing
from pathlib import Path

from networth.config import ConfigError
from networth.filelock import LockUnavailable, exclusive_file_lock
from networth.link_lifecycle import LifecycleError, mint_request
from networth.plaid.client import PlaidCallError, PlaidClient
from networth.plaid.environment import (
    Paths,
    PlaidEnvironment,
    load_credentials,
    paths_for,
    selected_environment,
)
from networth.plaid.rehearsal import COUNTRY_CODES
from networth.storage import migrate
from networth.tokenstore import TokenStore, TokenStoreError

SUMMARY = "Mint one Hosted Link request for the owner's driver (scripts/link.sh)."

DEPLOY_LOCK_NAME = ".runtime-deploy.lock"

#: Exception types whose text is fixed or self-redacting, and may be printed.
#: Anything else is reported by type alone: an unexpected exception raised next
#: to a provider reply is the one most likely to be carrying part of it.
PRINTABLE_FAILURES = (ConfigError, PlaidCallError, TokenStoreError, LifecycleError, LockUnavailable)


class Refusal(RuntimeError):
    """A fixed sentence this verb chose to print; nothing was minted."""


def deploy_lock_path(paths: Paths) -> Path:
    """The file ``install-runtime.sh`` locks: the service user's home directory.

    Derived from the selected database rather than spelled as an absolute path,
    so the installed host resolves it to ``/var/lib/networth/.runtime-deploy.lock``
    and a test resolves it inside its temporary directory.
    """
    return paths.database.parent.parent / DEPLOY_LOCK_NAME


def describe_failure(exc: BaseException) -> str:
    if isinstance(exc, (Refusal, *PRINTABLE_FAILURES)):
        return str(exc)
    return f"{type(exc).__name__} (detail withheld)"


def require_stated_environment(stated: str) -> PlaidEnvironment:
    """The environment this process was started in, if the caller named the same one."""
    environment = selected_environment()
    if environment.value != stated:
        raise Refusal(
            f"the driver asked for {stated!r} and this host was started for another "
            "environment; nothing was read or minted"
        )
    return environment


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--environment",
        required=True,
        choices=[environment.value for environment in PlaidEnvironment],
        help="the environment the calling driver believes it is in; a mismatch is refused",
    )


def run(args: argparse.Namespace) -> int:
    try:
        environment = require_stated_environment(args.environment)
        if not stat.S_ISFIFO(os.fstat(sys.stdout.fileno()).st_mode):
            raise Refusal(
                "this verb writes a link token and an openable URL to stdout for "
                "scripts/link.sh, and stdout is not a pipe; nothing was minted"
            )
        paths = paths_for(environment)
        with exclusive_file_lock(deploy_lock_path(paths), blocking=False):
            client = PlaidClient(load_credentials(environment))
            with closing(sqlite3.connect(paths.database.as_uri() + "?mode=rw", uri=True)) as db:
                migrate(db)
                result = mint_request(
                    db, TokenStore(paths.items), client, country_codes=COUNTRY_CODES
                )
    except Exception as exc:
        print(f"link-mint stopped: {describe_failure(exc)}", file=sys.stderr)
        return 2
    print(result.to_wire())
    return 0
