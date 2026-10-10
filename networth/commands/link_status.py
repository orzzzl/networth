"""``networth link-status`` — what this host knows about Link, as states and counts.

The owner's driver runs on the Mac and everything it has to decide is decided by
facts on this host: how many lifetime slots are left before it asks him to spend
one, whether his finished Link has been exchanged yet, whether the accounts
behind it were found and fetched. Nothing on ``main`` could answer those without
an agent reading SQL over SSH, which is not something an owner-run script can
depend on.

**States, counts and instants. No identity and no figure.** The output is one
marked JSON line, read by a program and shown to the owner as sentences. It
carries the flow id the caller already supplied and nothing else that names
anything: no Item or institution id, no account name, no balance. That makes it
safe in a transcript, which matters because the Sandbox rehearsal of this path
is run by agents.

**It reads; it never advances anything.** The lifecycle worker
(``networth-link.service``) is the only thing that polls the provider or
exchanges a token. A status read that could poll would put a second, unlocked
caller in front of a 30-minute exchange window.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from networth.commands.link_mint import (
    Refusal,
    describe_failure,
    require_stated_environment,
)
from networth.commands.link_mint import (
    add_arguments as add_environment_argument,
)
from networth.item_budget import ItemBudgetError, read_item_budget
from networth.plaid.environment import PlaidEnvironment, paths_for
from networth.storage import migrate

SUMMARY = "Report Link progress on this host as states and counts (no identities, no figures)."

STATUS_WIRE_MARKER = "networth-link-status-v1:"
_FLOW_ID = re.compile(r"[0-9a-f]{32}\Z")

#: ``link_result`` states, in the order the lifecycle moves through them.
RESULT_STATES = (
    "SUCCESS_PENDING_EXCHANGE",
    "EXCHANGING",
    "EXCHANGED",
    "TOKEN_EXPIRED",
    "EXCHANGE_UNCERTAIN",
)

#: The same predicate ``runtime.pending_flows`` and ``install-runtime.sh`` use.
_PENDING = (
    "SELECT count(*) FROM link_request WHERE material_reaped_at IS NULL "
    "OR EXISTS (SELECT 1 FROM link_result r WHERE r.flow_id = link_request.flow_id "
    "AND r.state = 'EXCHANGING')"
)

#: Local Item rows bought by this flow: an exchanged result names the Item.
_FLOW_ITEMS = (
    "SELECT i.id FROM item AS i JOIN link_result AS r ON r.item_id = i.plaid_item_id "
    "WHERE r.flow_id = ? AND r.state = 'EXCHANGED'"
)


def _count(db: sqlite3.Connection, sql: str, *parameters: object) -> int:
    row = db.execute(sql, parameters).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0


def _budget(db: sqlite3.Connection) -> dict[str, object]:
    try:
        budget = read_item_budget(db)
    except ItemBudgetError:
        # The detail may identify an Item. The refusal itself is the fact.
        return {"state": "UNAVAILABLE"}
    return {
        "state": "AVAILABLE",
        "capacity": budget.capacity,
        "spent": budget.spent_count,
        "remaining": budget.remaining,
        "in_flight": len(budget.in_flight),
        "stranded": len(budget.stranded),
    }


def _flow(db: sqlite3.Connection, flow_id: str, now: datetime) -> dict[str, object]:
    row = db.execute(
        "SELECT state, url_release_authorized_at, hosted_url_expires_at, last_poll_at, "
        "poll_error, polling_closed_at FROM link_request WHERE flow_id = ?",
        (flow_id,),
    ).fetchone()
    if row is None:
        return {"known": False}
    state, released_at, expires_at, last_poll_at, poll_error, closed_at = row
    expires = datetime.fromisoformat(expires_at)
    results = dict.fromkeys(RESULT_STATES, 0)
    for result_state, count in db.execute(
        "SELECT state, count(*) FROM link_result WHERE flow_id = ? GROUP BY state", (flow_id,)
    ):
        results[str(result_state)] = int(count)
    items = f"({_FLOW_ITEMS})"
    accounts = (
        f"FROM account AS a WHERE a.item_id IN {items} "
        "AND a.reconciliation_state <> 'ARCHIVED' AND a.archived_at IS NULL"
    )
    return {
        "known": True,
        "request": state,
        "released": released_at is not None,
        "url_expired": now >= expires,
        # Only a poll made after the URL died can say nothing finished through it.
        "polled_after_expiry": last_poll_at is not None
        and datetime.fromisoformat(last_poll_at) >= expires,
        "poll_failing": poll_error is not None,
        "closed": closed_at is not None,
        "sessions": _count(db, "SELECT count(*) FROM link_session WHERE flow_id = ?", flow_id),
        "results": results,
        "holds": _count(
            db,
            "SELECT count(*) FROM link_material_hold WHERE flow_id = ? AND resolved_at IS NULL",
            flow_id,
        ),
        "unidentified": _count(
            db,
            "SELECT count(*) FROM link_success_observation "
            "WHERE flow_id = ? AND resolved_at IS NULL",
            flow_id,
        ),
        "items": _count(db, f"SELECT count(*) FROM {items}", flow_id),
        "items_discovered": _count(
            db,
            f"SELECT count(*) FROM item WHERE id IN {items} AND accounts_discovered_at IS NOT NULL",
            flow_id,
        ),
        "accounts": _count(db, f"SELECT count(*) {accounts}", flow_id),
        "accounts_pending": _count(
            db, f"SELECT count(*) {accounts} AND a.reconciliation_state = 'NEW'", flow_id
        ),
        # An account is "in a snapshot" once a run that observed it produced one.
        "accounts_in_snapshot": _count(
            db,
            f"SELECT count(*) {accounts} AND EXISTS (SELECT 1 FROM observation AS o "
            "JOIN snapshot AS s ON s.sync_run_id = o.sync_run_id WHERE o.account_id = a.id)",
            flow_id,
        ),
    }


def collect(
    db: sqlite3.Connection,
    environment: PlaidEnvironment,
    *,
    flow_id: str | None,
    now: datetime,
) -> dict[str, Any]:
    """Everything the owner's driver decides from, read in one transaction."""
    if db.in_transaction:
        raise ValueError("status requires a connection with no active transaction")
    db.execute("BEGIN")
    try:
        latest_snapshot = db.execute("SELECT max(id) FROM snapshot").fetchone()[0]
        status: dict[str, Any] = {
            "environment": environment.value,
            "budget": _budget(db),
            "pending_requests": _count(db, _PENDING),
            "items": _count(db, "SELECT count(*) FROM item"),
            "paired": _count(db, "SELECT count(*) FROM pairing WHERE state = 'ACTIVE'") == 1,
            # True only when what the phone would fetch is the newest snapshot.
            "published_latest": latest_snapshot is not None
            and _count(
                db,
                "SELECT count(*) FROM published_envelope AS e JOIN publication AS p "
                "ON p.id = e.publication_id WHERE e.is_active = 1 AND p.snapshot_id = ?",
                latest_snapshot,
            )
            == 1,
        }
        if flow_id is not None:
            status["flow"] = _flow(db, flow_id, now)
        return status
    finally:
        db.rollback()


def to_wire(status: dict[str, Any]) -> str:
    return STATUS_WIRE_MARKER + json.dumps(status, sort_keys=True, separators=(",", ":"))


def from_wire(line: str) -> dict[str, Any]:
    if not line.startswith(STATUS_WIRE_MARKER):
        raise ValueError("not a link status line")
    status = json.loads(line[len(STATUS_WIRE_MARKER) :])
    if not isinstance(status, dict):
        raise ValueError("link status is not an object")
    return status


def add_arguments(parser: argparse.ArgumentParser) -> None:
    add_environment_argument(parser)
    parser.add_argument("--flow", help="the flow id the mint returned; omitted for a preflight")


def run(args: argparse.Namespace) -> int:
    try:
        environment = require_stated_environment(args.environment)
        if args.flow is not None and _FLOW_ID.match(args.flow) is None:
            raise Refusal("--flow is not a flow id (32 lowercase hex characters)")
        database = paths_for(environment).database
        with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True)) as db:
            # The daemon migrates on every open; a Sandbox database last touched
            # by an older rehearsal would otherwise lack the columns read below.
            migrate(db)
            status = collect(db, environment, flow_id=args.flow, now=datetime.now(UTC))
    except Exception as exc:
        print(f"link-status stopped: {describe_failure(exc)}", file=sys.stderr)
        return 2
    print(to_wire(status))
    return 0
