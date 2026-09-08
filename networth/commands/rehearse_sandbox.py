"""``networth rehearse-sandbox`` — task ``06``'s item → exchange → fetch, in Sandbox.

This verb is the wiring, and the wiring *is* two of the four acceptance criteria:
``NETWORTH_ENV`` selects the credential file, the items file and the database
**together** (§15), and a credential file whose own ``PLAID_ENV`` disagrees with the
selection is a **startup failure** rather than a run nobody questions. Both come from
:mod:`networth.plaid.environment`; what this module adds is that nothing else can be
reached without going through them, and that the paths chosen are *printed*, so the
transcript says which database the run could have written to.

**The report never contains a figure, a token, an ``item_id`` or an institution.**
It is presence and type per field — see :mod:`networth.plaid.observation` for why that
is the deliverable rather than a formality.
"""

from __future__ import annotations

import argparse
import sys

from networth.config import ConfigError
from networth.plaid.client import PlaidCallError
from networth.plaid.environment import (
    PlaidEnvironment,
    load_credentials,
    paths_for,
    selected_environment,
)
from networth.plaid.observation import RecordSet
from networth.plaid.rehearsal import (
    SANDBOX_PASSWORD,
    SANDBOX_USERNAME,
    RehearsalError,
    RehearsalOutcome,
    SandboxRehearsal,
)
from networth.tokenstore import TokenStore, TokenStoreError

SUMMARY = "Rehearse the Plaid Link flow end to end against Sandbox (task 06)."

# Wide enough for the longest path in the three field lists, so the notes line up
# into a column that can be read down for absences.
_FIELD_COLUMN = 38


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--print-paths-only",
        action="store_true",
        help=(
            "resolve the environment and print the paths it selects, then stop — "
            "makes no Plaid call"
        ),
    )


def _render(record_set: RecordSet) -> list[str]:
    lines = [f"{record_set.name} ({record_set.count} records)"]
    lines += [f"  {field.path:<{_FIELD_COLUMN}}{field.note}" for field in record_set.fields]
    return lines


def _report(outcome: RehearsalOutcome) -> str:
    sections = [
        _render(outcome.accounts),
        _render(outcome.holdings),
        _render(outcome.securities),
    ]
    return "\n\n".join("\n".join(section) for section in sections)


def run(args: argparse.Namespace) -> int:
    try:
        environment = selected_environment()
        paths = paths_for(environment)
        print(f"environment   {environment.value}")
        print(f"credentials   {paths.credentials}")
        print(f"token store   {paths.items}")
        print(f"database      {paths.database}")
        if environment is not PlaidEnvironment.SANDBOX:
            # Before `load_credentials`, so the Production secret is not even read
            # into this process. The rehearsal's constructor refuses too; this is the
            # one that keeps the refusal cheap and legible at the boundary the
            # operator actually sees.
            print(
                f"refusing to rehearse against {environment.value!r}: a Link is what "
                "spends a lifetime Item slot (F2a), so it is rehearsed in Sandbox and "
                "nowhere else",
                file=sys.stderr,
            )
            return 2
        if args.print_paths_only:
            return 0
        credentials = load_credentials(environment)
        rehearsal = SandboxRehearsal(
            credentials,
            token_store=TokenStore(paths.items),
            database=paths.database,
        )
        outcome = rehearsal.run()
    except (ConfigError, RehearsalError, PlaidCallError, TokenStoreError) as exc:
        # Every one of these is already redacted by the module that raised it: no
        # response body, no credential, no token material. Printing `exc` is safe
        # exactly because that is a property of those types and not of this handler.
        print(f"rehearsal failed: {exc}", file=sys.stderr)
        return 2

    # Not "link completed". `/sandbox/public_token/create` is Plaid's documented
    # *Link bypass* ("Skip Link" in Sandbox Studio): it mints an Item and a
    # `public_token` with no Link UI. Printing a completed Link here would record the
    # bypass as the thing it bypasses, and task `06a` — which proves a real Hosted
    # Link — would then be verifying something the transcript already claimed.
    print(
        f"item          created via /sandbox/public_token/create as "
        f"{SANDBOX_USERNAME}/{SANDBOX_PASSWORD} — Plaid's Link bypass, not a Link run"
    )
    print("link (real)   not exercised here; a completed Hosted Link is task 06a's")
    print("exchange      access_token stored through TokenStore before any item row")
    print()
    print(_report(outcome))
    return 0
