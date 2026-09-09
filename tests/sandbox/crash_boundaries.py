"""Measurement (iii) of task `06a`: the four crash boundaries around the exchange.

`DESIGN.md` §7 and §14a order the exchange as claim → send → response → `fsync`
→ DB commit, and issues #5 and #15 name the crash windows between those steps.
This rig injects a failure at each boundary, restarts, and records **what a
fresh worker can conclude from the disk it wakes up to**.

Two rules give the measurement its meaning, and both are enforced here rather
than asserted in prose:

**Ground truth is held only by the ledger, and recovery may not read it.**
:class:`PlaidLedger` knows whether the single-use ``public_token`` was really
consumed. :func:`recover` is handed the database and the token store and
nothing else. That separation is the whole experiment: the question is not what
happened, it is what a restarted worker can *tell* happened.

**What Plaid does on a duplicate exchange is not known yet, so it is not
assumed.** That is measurement (ii), and it is live-only. The ledger therefore
refuses to answer for it — see :class:`DuplicateExchangeNotMeasured`. A rig that
quietly returned some plausible error here would be the guessed fixture this
task exists to eliminate, and every conclusion drawn from it would inherit the
guess.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from networth.storage import migrate
from networth.tokenstore import SecretKind, TokenStore

#: Fixed instants. Nothing here measures time; the clocks are (i)'s subject.
MINTED_AT = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
FINISHED_AT = datetime(2026, 2, 1, 12, 5, tzinfo=UTC)
NOW = datetime(2026, 2, 1, 12, 6, tzinfo=UTC)

#: Stand-in material. Shaped like nothing in particular, on purpose (AGENTS.md 0).
MATERIAL = "rig-material-not-a-credential"


class Boundary(Enum):
    """Where the worker dies, named by what has and has not happened."""

    BEFORE_SEND = "before send"
    AFTER_SEND_BEFORE_RESPONSE = "after send / before response"
    AFTER_RESPONSE_BEFORE_FSYNC = "after response / before fsync"
    AFTER_FSYNC_BEFORE_COMMIT = "after fsync / before the DB commit"
    NO_CRASH = "no crash (control)"


class Outcome(Enum):
    """What recovery concluded, from local evidence alone."""

    #: The credential is provably durable. Finish the local transaction and do
    #: **not** call Plaid again. This is the boundary rev 18 got wrong.
    COMPLETED_LOCALLY = "completed locally, no second exchange"

    #: Local evidence is exhausted: the token may or may not have been consumed
    #: and nothing on this disk can say which. Only Plaid can adjudicate, and
    #: what it answers is measurement (ii).
    NEEDS_PLAID_ADJUDICATION = "undecidable locally; only Plaid can say"

    #: A *successful* response is recorded and no material survived. The token
    #: was certainly consumed and the credential is certainly gone — which is a
    #: strictly stronger statement than ``EXCHANGE_UNCERTAIN``, and §7 has no
    #: state for it. See the module docstring's note on `07a`.
    STRANDED_KNOWN = "exchange known to have succeeded; credential lost"

    #: Nothing to recover — the flow already reached a terminal state.
    ALREADY_TERMINAL = "already terminal"


class Crashed(RuntimeError):
    """The injected failure. Stands in for the process dying at that instant."""


class DuplicateExchangeNotMeasured(RuntimeError):
    """The rig was asked what Plaid returns for an already-consumed token.

    Measurement (ii) answers this and has not run. Raising is the point: a
    default here would let a test pass on an assumption about the one behaviour
    `07a`'s ``EXCHANGE_UNCERTAIN`` handling is written against.
    """


@dataclass
class PlaidLedger:
    """The remote side — and the only holder of the ground truth.

    ``consumed`` is what actually became of the single-use ``public_token``.
    :func:`recover` never receives this object.
    """

    consumed: bool = False
    calls: int = 0

    def exchange(self, public_token: str) -> str:
        """Consume the token and return material, or refuse to guess."""

        self.calls += 1
        if self.consumed:
            raise DuplicateExchangeNotMeasured(
                "what Plaid returns for a re-exchanged public_token is "
                "measurement (ii); it has not been measured, so this rig will "
                "not invent it"
            )
        self.consumed = True
        return MATERIAL


@dataclass(frozen=True)
class Observation:
    """Everything a restarted worker can see. Deliberately no ground truth."""

    flow_state: str
    secret_ref_in_db: str | None
    material_durable: bool
    item_row_exists: bool
    exchange_attempts: int
    request_id_recorded: str | None


def open_database(path: Path) -> sqlite3.Connection:
    """A fresh connection with the integrity setting every repository requires.

    ``PRAGMA foreign_keys`` is per-connection and a silent no-op inside a
    transaction, so it is set here, on a connection that has not begun one.
    """

    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def seed(connection: sqlite3.Connection, flow_id: str) -> None:
    """A flow that has completed Link and is waiting to be exchanged.

    By **F2a** the Item already exists at Plaid here: the slot is spent and the
    30-minute exchange clock is running.
    """

    migrate(connection)
    connection.execute(
        "INSERT INTO institution (plaid_institution_id, name, is_oauth) VALUES (?, ?, 0)",
        ("ins-rig", "Rig Institution"),
    )
    connection.execute(
        """
        INSERT INTO link_flow (
            flow_id, minted_at, hosted_url_expires_at, started_at, finished_at,
            token_exchange_expires_at, state, exchange_attempts
        ) VALUES (?, ?, ?, ?, ?, ?, 'SUCCESS_PENDING_EXCHANGE', 0)
        """,
        (
            flow_id,
            MINTED_AT.isoformat(),
            (MINTED_AT.replace(minute=30)).isoformat(),
            FINISHED_AT.isoformat(),
            FINISHED_AT.isoformat(),
            (FINISHED_AT.replace(minute=35)).isoformat(),
        ),
    )


def run_exchange(
    *,
    boundary: Boundary,
    connection: sqlite3.Connection,
    store: TokenStore,
    ledger: PlaidLedger,
    flow_id: str,
    public_token: str = "public-token-rig",
    request_arrived: bool = True,
    persist_request_id: bool = True,
) -> None:
    """One exchange attempt that dies at ``boundary``.

    ``request_arrived`` only matters at :attr:`Boundary.AFTER_SEND_BEFORE_RESPONSE`,
    which is the boundary whose *ground truth* is itself two-valued: a worker
    that died mid-call cannot know whether the request reached Plaid. Both
    worlds are reachable here so the measurement covers the one that makes the
    boundary uncertain rather than only the convenient one.

    ``persist_request_id`` exists because the third boundary's decidability
    turns out to rest entirely on it, which was not obvious until this rig was
    run. Setting it ``False`` models a worker that does not durably record a
    successful response before writing the credential — and collapses boundary
    three into boundaries one and two. It is a knob so that the dependency can
    be *demonstrated* rather than claimed.
    """

    # --- claim (§7): at most one worker, enforced in the database ------------
    cursor = connection.execute(
        """
        UPDATE link_flow
           SET state = 'EXCHANGING', exchange_claimed_at = ?, exchange_claim_owner = ?,
               exchange_attempts = exchange_attempts + 1
         WHERE flow_id = ? AND state = 'SUCCESS_PENDING_EXCHANGE'
        """,
        (NOW.isoformat(), "rig-worker", flow_id),
    )
    if cursor.rowcount != 1:
        raise AssertionError("the rig did not win its own claim")
    row = connection.execute(
        "SELECT id, exchange_attempts FROM link_flow WHERE flow_id = ?", (flow_id,)
    ).fetchone()
    link_flow_id, attempt_number = row
    # `request_id` is NULL until a response carries one. This row is the closest
    # thing to a record of intent that exists, and (iii) is partly a test of
    # whether it separates "about to send" from "sent".
    connection.execute(
        "INSERT INTO link_exchange_attempt (link_flow_id, attempt_number, request_id) "
        "VALUES (?, ?, NULL)",
        (link_flow_id, attempt_number),
    )

    if boundary is Boundary.BEFORE_SEND:
        raise Crashed(Boundary.BEFORE_SEND.value)

    # --- send + response -----------------------------------------------------
    if boundary is Boundary.AFTER_SEND_BEFORE_RESPONSE:
        if request_arrived:
            ledger.exchange(public_token)
        raise Crashed(Boundary.AFTER_SEND_BEFORE_RESPONSE.value)

    material = ledger.exchange(public_token)
    if persist_request_id:
        # Written **only after a success**, and that is load-bearing rather than
        # incidental: Plaid returns a `request_id` on errors too, so a row that
        # merely recorded "a response arrived" would not tell a consumed token
        # from a refused one. What makes this row evidence is that reaching it
        # means the exchange returned material.
        connection.execute(
            "UPDATE link_exchange_attempt SET request_id = ? "
            "WHERE link_flow_id = ? AND attempt_number = ?",
            ("req-rig", link_flow_id, attempt_number),
        )

    if boundary is Boundary.AFTER_RESPONSE_BEFORE_FSYNC:
        raise Crashed(Boundary.AFTER_RESPONSE_BEFORE_FSYNC.value)

    # --- durability, then the row that references it (§14a ordering) ---------
    secret_ref = store.put(SecretKind.ACCESS_TOKEN, flow_id, material, item_id="item-rig")

    if boundary is Boundary.AFTER_FSYNC_BEFORE_COMMIT:
        raise Crashed(Boundary.AFTER_FSYNC_BEFORE_COMMIT.value)

    commit_local_transaction(connection, flow_id=flow_id, secret_ref=secret_ref)


def commit_local_transaction(
    connection: sqlite3.Connection, *, flow_id: str, secret_ref: str
) -> None:
    """Write the ``item`` row and mark the flow ``EXCHANGED``, in one transaction.

    Reached both by the happy path and by recovery from
    :attr:`Boundary.AFTER_FSYNC_BEFORE_COMMIT` — deliberately the *same* code,
    because "recovery completes the local transaction" is only true if it is
    the same transaction and not a second, subtly different one.
    """

    with connection:
        connection.execute("BEGIN")
        connection.execute(
            """
            INSERT INTO item (
                institution_id, plaid_item_id, secret_ref, status, status_since, created_at
            ) VALUES ((SELECT id FROM institution), ?, ?, 'HEALTHY', ?, ?)
            """,
            ("item-rig", secret_ref, NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "UPDATE link_flow SET state = 'EXCHANGED', secret_ref = ?, item_id = ? "
            "WHERE flow_id = ?",
            (secret_ref, "item-rig", flow_id),
        )


def observe(connection: sqlite3.Connection, store: TokenStore, flow_id: str) -> Observation:
    """What a fresh process sees. No ground truth reaches this function."""

    state, secret_ref, attempts = connection.execute(
        "SELECT state, secret_ref, exchange_attempts FROM link_flow WHERE flow_id = ?",
        (flow_id,),
    ).fetchone()
    request_id_row = connection.execute(
        "SELECT request_id FROM link_exchange_attempt "
        "JOIN link_flow ON link_flow.id = link_exchange_attempt.link_flow_id "
        "WHERE link_flow.flow_id = ? ORDER BY attempt_number DESC LIMIT 1",
        (flow_id,),
    ).fetchone()
    item_row = connection.execute("SELECT 1 FROM item WHERE plaid_item_id = 'item-rig'").fetchone()
    return Observation(
        flow_state=state,
        secret_ref_in_db=secret_ref,
        material_durable=store.reconcile(flow_id) is not None,
        item_row_exists=item_row is not None,
        exchange_attempts=attempts,
        request_id_recorded=request_id_row[0] if request_id_row else None,
    )


def recover(connection: sqlite3.Connection, store: TokenStore, flow_id: str) -> Outcome:
    """The recovery routine under measurement (issues #5, #15).

    It receives the database and the token store — never the ledger. Every
    conclusion it reaches is therefore one a real restarted worker could reach.
    """

    state = connection.execute(
        "SELECT state FROM link_flow WHERE flow_id = ?", (flow_id,)
    ).fetchone()[0]
    if state in {"EXCHANGED", "EXCHANGE_UNCERTAIN", "TOKEN_EXPIRED", "SESSION_EXITED"}:
        return Outcome.ALREADY_TERMINAL

    # Issue #15: ask whether the credential is already durable **before**
    # classifying a stale claim or calling Plaid. Doing this second is what
    # turns a recoverable crash into a re-spent lifetime slot.
    record = store.reconcile(flow_id)
    if record is not None:
        commit_local_transaction(connection, flow_id=flow_id, secret_ref=record.secret_ref)
        return Outcome.COMPLETED_LOCALLY

    # No material. The remaining question is whether the exchange is *known* to
    # have succeeded, and the only local evidence for that is a `request_id`
    # recorded against the attempt — written after a success and nowhere else.
    succeeded = connection.execute(
        "SELECT request_id FROM link_exchange_attempt "
        "JOIN link_flow ON link_flow.id = link_exchange_attempt.link_flow_id "
        "WHERE link_flow.flow_id = ? ORDER BY attempt_number DESC LIMIT 1",
        (flow_id,),
    ).fetchone()
    if succeeded is not None and succeeded[0] is not None:
        return Outcome.STRANDED_KNOWN

    return Outcome.NEEDS_PLAID_ADJUDICATION
