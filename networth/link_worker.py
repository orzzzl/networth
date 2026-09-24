"""One automatic Link polling/exchange pass, serialized per database and request.

This lock and the conditional SQL claim fence only workers sharing this database.
They do not fence lost-host recovery (07b), which requires powering off that host.
No uncertain send is retried, even when a provider accepts duplicate exchanges.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from networth.filelock import LockUnavailable, exclusive_file_lock
from networth.link_finalization import MetadataClient, finalize_durable_result
from networth.link_observations import ObservedToken, ingest_poll
from networth.link_reconciliation import reconcile_request
from networth.plaid.client import ExchangedItem, LinkSessionPoll, PlaidCallError
from networth.tokenstore import SecretKind, TokenStore, TokenStoreError, new_flow_id, secret_ref_for


class WorkerError(RuntimeError):
    """Redacted refusal; database and material remain the recovery record."""


class LinkClient(MetadataClient, Protocol):
    def link_token_get(self, link_token: str) -> LinkSessionPoll: ...

    def item_public_token_exchange(self, public_token: str) -> ExchangedItem: ...


@dataclass(frozen=True, slots=True)
class WorkerOutcome:
    busy: bool = False
    polled: bool = False
    held: bool = False
    finalized: int = 0
    failed: bool = False


def _stamp(now: datetime) -> str:
    if now.utcoffset() is None:
        raise WorkerError("worker requires an aware clock")
    return now.astimezone(UTC).isoformat().replace("+00:00", "Z")


def request_lock_path(db: sqlite3.Connection, flow_id: str) -> Path:
    secret_ref_for(SecretKind.LINK_TOKEN, flow_id)
    name = next(row[2] for row in db.execute("PRAGMA database_list") if row[1] == "main")
    if not name:
        raise WorkerError("worker requires a file-backed database")
    path = Path(name).resolve()
    return path.with_name(path.name + ".link-" + flow_id + ".lock")


def _held(db: sqlite3.Connection, flow_id: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM link_success_observation WHERE flow_id = ? AND resolved_at IS NULL "
            "UNION ALL SELECT 1 FROM link_material_hold "
            "WHERE flow_id = ? AND resolved_at IS NULL LIMIT 1",
            (flow_id, flow_id),
        ).fetchone()
        is not None
    )


def _material_hold(db: sqlite3.Connection, flow: str, now: datetime) -> None:
    with db:
        db.execute(
            "INSERT INTO link_material_hold(hold_id, flow_id, reason, observed_at) "
            "VALUES (?, ?, 'UNVERIFIED_MATERIAL', ?) ON CONFLICT DO NOTHING",
            (new_flow_id(), flow, _stamp(now)),
        )


def _recover(
    db: sqlite3.Connection,
    store: TokenStore,
    client: LinkClient,
    *,
    flow_id: str,
    country_codes: Sequence[str],
    clock: Callable[[], datetime],
) -> tuple[int, bool, bool]:
    snapshot = reconcile_request(db, store, flow_id=flow_id, now=clock())
    if snapshot.hold_ids:
        return 0, True, False
    # Holding the request lock proves no other conforming worker still owns an
    # EXCHANGING claim. Only the reconciler's all-candidate absence allows this.
    with db:
        for rid in snapshot.absent_result_ids:
            deadline = db.execute(
                "SELECT token_exchange_expires_at FROM link_result WHERE result_id = ?", (rid,)
            ).fetchone()[0]
            if deadline is not None and clock() >= datetime.fromisoformat(deadline):
                db.execute(
                    "UPDATE link_result SET state = 'TOKEN_EXPIRED' WHERE result_id = ? "
                    "AND state = 'SUCCESS_PENDING_EXCHANGE' AND exchange_attempts = 0",
                    (rid,),
                )
            db.execute(
                "UPDATE link_result SET state = 'EXCHANGE_UNCERTAIN' WHERE result_id = ? "
                "AND (state = 'EXCHANGING' OR "
                "(state = 'SUCCESS_PENDING_EXCHANGE' AND exchange_attempts > 0))",
                (rid,),
            )
    finalized = 0
    failed = False
    for material in snapshot.materials:
        state, captured_ref = db.execute(
            "SELECT state, secret_ref FROM link_result WHERE result_id = ?", (material.result_id,)
        ).fetchone()
        if state == "EXCHANGED":
            continue
        ref = captured_ref or material.secret_refs[0]
        # Found, attributed material can recover even an uncertain/expired row;
        # this is a local metadata/Item commit, never another exchange.
        with db:
            db.execute(
                "UPDATE link_result SET state = 'EXCHANGING', item_id = ?, secret_ref = ? "
                "WHERE result_id = ? AND state != 'EXCHANGED'",
                (material.item_id, ref, material.result_id),
            )
        try:
            finalize_durable_result(
                db,
                store,
                client,
                result_id=material.result_id,
                secret_ref=ref,
                country_codes=country_codes,
                now=clock(),
            )
            finalized += 1
        except (TokenStoreError, OSError):
            _material_hold(db, flow_id, clock())
            return finalized, True, True
        except Exception:
            # Includes malformed metadata and disk faults: never print provider
            # or filesystem exception text. The durable credential remains.
            failed = True
    return finalized, False, failed


def _claim(db: sqlite3.Connection, rid: str, now: datetime) -> bool:
    stamp = _stamp(now)
    with db:
        row = db.execute(
            "SELECT token_exchange_expires_at FROM link_result WHERE result_id = ?", (rid,)
        ).fetchone()
        if row is None or row[0] is None:
            return False
        if now >= datetime.fromisoformat(row[0]):
            db.execute(
                "UPDATE link_result SET state = 'TOKEN_EXPIRED' "
                "WHERE result_id = ? AND state = 'SUCCESS_PENDING_EXCHANGE'",
                (rid,),
            )
            return False
        # This serializes workers sharing this database file only. A zero-row
        # update must never reach Plaid. The attempt is durable before the send.
        changed = db.execute(
            "UPDATE link_result SET state = 'EXCHANGING', exchange_claimed_at = ?, "
            "exchange_claim_owner = ?, exchange_attempts = exchange_attempts + 1 "
            "WHERE result_id = ? AND state = 'SUCCESS_PENDING_EXCHANGE' "
            "AND exchange_attempts = 0 AND item_id IS NULL AND secret_ref IS NULL",
            (stamp, new_flow_id(), rid),
        ).rowcount
        if changed != 1:
            return False
        db.execute(
            "INSERT INTO link_result_attempt(result_id, attempt_number) "
            "SELECT result_id, exchange_attempts FROM link_result WHERE result_id = ?",
            (rid,),
        )
    return True


def _capture(
    db: sqlite3.Connection,
    rid: str,
    *,
    item_id: str | None,
    request_id: str | None,
) -> None:
    # Independent of credential writes: a later put/fsync/metadata failure must
    # not erase the provider's already-visible support identifiers.
    with db:
        db.execute(
            "UPDATE link_result SET item_id = coalesce(?, item_id) WHERE result_id = ?",
            (item_id, rid),
        )
        db.execute(
            "UPDATE link_result_attempt SET request_id = ? WHERE result_id = ? "
            "AND attempt_number = (SELECT exchange_attempts FROM link_result WHERE result_id = ?)",
            (request_id, rid, rid),
        )


def _exchange(
    db: sqlite3.Connection,
    store: TokenStore,
    client: LinkClient,
    token: ObservedToken,
    *,
    link_token: str,
) -> bool:
    try:
        response = client.item_public_token_exchange(token.public_token.reveal())
    except Exception as exc:
        if isinstance(exc, PlaidCallError):
            bearers = {link_token, token.public_token.reveal()}
            _capture(
                db,
                token.result_id,
                item_id=exc.item_id if exc.item_id not in bearers else None,
                request_id=exc.request_id if exc.request_id not in bearers else None,
            )
        with db:
            db.execute(
                "UPDATE link_result SET state = 'EXCHANGE_UNCERTAIN' WHERE result_id = ?",
                (token.result_id,),
            )
        return False
    bearers = {link_token, token.public_token.reveal(), response.access_token}
    valid_item = isinstance(response.item_id, str) and bool(response.item_id.strip())
    valid_item = valid_item and response.item_id not in bearers
    _capture(
        db,
        token.result_id,
        item_id=response.item_id if valid_item else None,
        request_id=response.request_id if response.request_id not in bearers else None,
    )
    if not valid_item or not response.access_token:
        with db:
            db.execute(
                "UPDATE link_result SET state = 'EXCHANGE_UNCERTAIN' WHERE result_id = ?",
                (token.result_id,),
            )
        return False
    # Issue #42: both values are in hand here; reject equality before any put.
    # A failure here leaves EXCHANGING. The next pass must reconcile any partial
    # file through its durability barrier before deciding found/absent/hold.
    store.put(
        SecretKind.ACCESS_TOKEN,
        token.result_id,
        response.access_token,
        item_id=response.item_id,
    )
    return True


def run_request(
    db: sqlite3.Connection,
    store: TokenStore,
    client: LinkClient,
    *,
    flow_id: str,
    country_codes: Sequence[str],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> WorkerOutcome:
    """Recover, poll, ingest, reconcile, conditionally exchange, then finalize.

    No human completion signal is accepted or required. A scheduler calls this
    immediately after mint and on subsequent ticks; this function performs one
    bounded pass. It neither mints URLs nor classifies/reaps requests.
    """
    if db.in_transaction:
        raise WorkerError("worker requires no active transaction")
    _stamp(clock())
    try:
        with exclusive_file_lock(request_lock_path(db, flow_id), blocking=False):
            return _run_locked(db, store, client, flow_id, country_codes, clock)
    except LockUnavailable:
        return WorkerOutcome(busy=True)
    except Exception:
        db.rollback()
        raise WorkerError(
            "Link worker pass failed; retained evidence requires reinspection"
        ) from None


def _run_locked(
    db: sqlite3.Connection,
    store: TokenStore,
    client: LinkClient,
    flow: str,
    countries: Sequence[str],
    clock: Callable[[], datetime],
) -> WorkerOutcome:
    request = db.execute(
        "SELECT secret_ref, material_reaped_at, polling_closed_at "
        "FROM link_request WHERE flow_id = ?",
        (flow,),
    ).fetchone()
    if request is None:
        raise WorkerError("request is not recorded")
    finalized, held, failed = _recover(
        db,
        store,
        client,
        flow_id=flow,
        country_codes=countries,
        clock=clock,
    )
    if held or request[1] is not None or request[2] is not None:
        return WorkerOutcome(held=held or _held(db, flow), finalized=finalized, failed=failed)
    try:
        ref = secret_ref_for(SecretKind.LINK_TOKEN, flow)
        if request[0] != ref:
            raise WorkerError("request link material reference is unavailable")
        key = store.get(ref).reveal()
        poll = client.link_token_get(key)
        evidence = ingest_poll(db, store, flow_id=flow, poll=poll, now=clock())
    except Exception:
        with db:
            db.execute(
                "UPDATE link_request SET poll_error = 'POLL_FAILED' WHERE flow_id = ?",
                (flow,),
            )
            db.execute(
                "INSERT INTO link_poll_history(flow_id, observed_at, outcome) VALUES (?, ?, "
                "'FAILED')",
                (flow, _stamp(clock())),
            )
        return WorkerOutcome(finalized=finalized, failed=True)
    snapshot = reconcile_request(db, store, flow_id=flow, now=clock())
    if snapshot.hold_ids or _held(db, flow):
        return WorkerOutcome(polled=True, held=True, finalized=finalized, failed=failed)
    absent = set(snapshot.absent_result_ids)
    for token in evidence.tokens:
        if token.result_id not in absent or not _claim(db, token.result_id, clock()):
            continue
        try:
            if not _exchange(db, store, client, token, link_token=key):
                failed = True
        except (TokenStoreError, OSError):
            _material_hold(db, flow, clock())
            return WorkerOutcome(polled=True, held=True, finalized=finalized, failed=True)
    # Reconcile again after all writes, including same-Item/distinct-Item results.
    # Metadata outage leaves material durable and EXCHANGING for a later pass.
    completed, held, recovery_failed = _recover(
        db,
        store,
        client,
        flow_id=flow,
        country_codes=countries,
        clock=clock,
    )
    return WorkerOutcome(
        polled=True,
        held=held,
        finalized=finalized + completed,
        failed=failed or recovery_failed,
    )
