"""Conservative request closure and automatic Link release (07a contracts A).

Every destructive decision shares the worker's request lock and ingestion's SQL
write lock. Provider absence never proves an exposed request spent zero slots.
"""

from __future__ import annotations

import hmac
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from networth.filelock import LockUnavailable, exclusive_file_lock
from networth.item_budget import read_item_budget
from networth.link_reconciliation import reconcile_request
from networth.link_recovery import AUTOMATIC_PROTOCOL, MintResult, RecoveryRecord
from networth.link_worker import (
    LinkClient,
    WorkerOutcome,
    _held,
    _run_locked,
    _stamp,
    request_lock_path,
)
from networth.mac_identity import REQUIRED_HOLDER
from networth.plaid.client import HostedLinkToken
from networth.plaid.rehearsal import REQUIRED_PRODUCTS
from networth.tokenstore import Secret, SecretKind, TokenStore, new_flow_id, secret_ref_for


class LifecycleError(RuntimeError):
    """A fixed diagnostic with no credential, provider reply or input content."""


class MintClient(LinkClient, Protocol):
    def link_token_create_hosted(
        self,
        *,
        client_user_id: str,
        client_name: str,
        products: Sequence[str],
        country_codes: Sequence[str],
        language: str,
        url_lifetime_seconds: int | None = None,
        completion_redirect_uri: str | None = None,
    ) -> HostedLinkToken: ...


def mint_request(
    db: sqlite3.Connection,
    store: TokenStore,
    client: MintClient,
    *,
    country_codes: Sequence[str],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> MintResult:
    if db.in_transaction:
        raise LifecycleError("mint requires no active transaction")
    if read_item_budget(db).remaining <= 0:
        raise LifecycleError("no Item budget remains")
    flow = new_flow_id()
    now = clock()
    minted = client.link_token_create_hosted(
        client_user_id=flow,
        client_name="networth",
        products=REQUIRED_PRODUCTS,
        country_codes=country_codes,
        language="en",
        url_lifetime_seconds=1800,
    )
    if minted.expires_at is None or minted.expires_at <= now:
        raise LifecycleError("provider mint expiration is unavailable")
    ref = store.put(SecretKind.LINK_TOKEN, flow, minted.link_token)
    with db:
        db.execute(
            "INSERT INTO link_request(flow_id, secret_ref, minted_at, hosted_url_expires_at, "
            "state, lifecycle_protocol) VALUES (?, ?, ?, ?, 'URL_MINTED', ?)",
            (flow, ref, _stamp(now), _stamp(minted.expires_at), AUTOMATIC_PROTOCOL),
        )
    # A failure leaves a durable request. It is never permission to remint.
    run_lifecycle(db, store, client, flow_id=flow, country_codes=country_codes, clock=clock)
    return MintResult(
        flow,
        Secret(minted.link_token),
        now,
        minted.expires_at,
        minted.url_lifetime_seconds,
        minted.hosted_link_url,
    )


def authorize_release(
    db: sqlite3.Connection,
    store: TokenStore,
    record: RecoveryRecord,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Authenticated driver return leg; commit before the caller acknowledges.

    SSH authenticates the caller; token possession binds this return to the
    request. The holder timestamp is an attestation, never a VPS deadline.
    """
    if db.in_transaction:
        raise LifecycleError("release requires no active transaction")
    if (
        record.protocol != AUTOMATIC_PROTOCOL
        or record.hosted_url is None
        or record.second_copy_holder != REQUIRED_HOLDER
        or record.second_copy_verified_at is None
    ):
        raise LifecycleError("automatic second-copy attestation is required")
    flow = record.flow_id
    with exclusive_file_lock(request_lock_path(db, flow)):
        try:
            db.execute("BEGIN IMMEDIATE")
            now = clock()
            row = db.execute(
                "SELECT lifecycle_protocol, secret_ref, hosted_url_expires_at, "
                "polling_closed_at, abandon_requested_at, material_reaped_at, state "
                "FROM link_request WHERE flow_id = ?",
                (flow,),
            ).fetchone()
            ref = secret_ref_for(SecretKind.LINK_TOKEN, flow)
            if (
                row is None
                or row[0] != AUTOMATIC_PROTOCOL
                or row[1] != ref
                or now >= datetime.fromisoformat(row[2])
                or any(x is not None for x in row[3:6])
                or row[6] != "URL_MINTED"
                or not hmac.compare_digest(store.get(ref).reveal(), record.link_token.reveal())
            ):
                raise LifecycleError("request cannot authorize URL release")
            db.execute(
                "UPDATE link_request SET second_copy_verified_at = "
                "coalesce(second_copy_verified_at, ?), "
                "second_copy_holder = coalesce(second_copy_holder, ?), "
                "url_release_authorized_at = coalesce(url_release_authorized_at, ?) WHERE "
                "flow_id = ?",
                (_stamp(record.second_copy_verified_at), REQUIRED_HOLDER, _stamp(now), flow),
            )
            db.commit()
        except BaseException:
            db.rollback()
            raise


def _coverage(db: sqlite3.Connection, flow: str, now: datetime) -> None:
    # Once adjudicated, an empty poll must not reopen this set. ingest_poll does
    # reopen it on new success evidence, retaining every previous audit decision.
    if (
        db.execute(
            "SELECT 1 FROM link_success_observation WHERE flow_id = ? AND reason = "
            "'COVERAGE_UNPROVEN'",
            (flow,),
        ).fetchone()
        is None
    ):
        db.execute(
            "INSERT INTO link_success_observation(observation_id, flow_id, reason, observed_at, "
            "last_observed_at) VALUES (?, ?, 'COVERAGE_UNPROVEN', ?, ?)",
            (new_flow_id(), flow, _stamp(now), _stamp(now)),
        )


def _uncertain_children(db: sqlite3.Connection, flow: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM link_session WHERE flow_id = ? AND finished_at IS NULL "
            "UNION ALL SELECT 1 FROM link_result WHERE flow_id = ? AND "
            "(state IN ('SUCCESS_PENDING_EXCHANGE', 'EXCHANGING') OR finished_at IS NULL) LIMIT 1",
            (flow, flow),
        ).fetchone()
        is not None
    )


def _finish_locked(
    db: sqlite3.Connection,
    store: TokenStore,
    flow: str,
    now: datetime,
    *,
    audit_id: str | None = None,
) -> tuple[bool, bool]:
    """Caller holds request lock; return closed/reaped. Never delete access material."""
    snapshot = reconcile_request(db, store, flow_id=flow, now=now)
    if snapshot.hold_ids:
        return False, False
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT lifecycle_protocol, url_release_authorized_at, hosted_url_expires_at, "
            "abandon_requested_at, polling_closed_at, material_reaped_at, last_poll_at, "
            "poll_error, minted_at, secret_ref FROM link_request WHERE flow_id = ?",
            (flow,),
        ).fetchone()
        if row is None:
            raise LifecycleError("request is not recorded")
        if row[9] not in (None, secret_ref_for(SecretKind.LINK_TOKEN, flow)):
            db.execute(
                "INSERT INTO link_material_hold(hold_id, flow_id, reason, observed_at) "
                "VALUES (?, ?, 'ATTRIBUTION_AMBIGUOUS', ?) ON CONFLICT DO NOTHING",
                (new_flow_id(), flow, _stamp(now)),
            )
            db.commit()
            return False, False
        native_unreleased = row[0] == AUTOMATIC_PROTOCOL and row[1] is None
        expired = now >= datetime.fromisoformat(row[2])
        closed = row[4] is not None
        has_evidence = (
            db.execute(
                "SELECT 1 FROM link_session WHERE flow_id = ? UNION ALL "
                "SELECT 1 FROM link_result WHERE flow_id = ? UNION ALL "
                "SELECT 1 FROM link_success_observation WHERE flow_id = ? LIMIT 1",
                (flow, flow, flow),
            ).fetchone()
            is not None
        )
        local_proof = native_unreleased and not has_evidence
        # Persist gaps, including mint-to-first-poll and restart-to-current-pass.
        instants = [datetime.fromisoformat(row[8])]
        instants.extend(
            datetime.fromisoformat(r[0])
            for r in db.execute(
                "SELECT observed_at FROM link_poll_history WHERE flow_id = ? AND outcome = "
                "'OBSERVED' "
                "ORDER BY observed_at",
                (flow,),
            )
        )
        instants.append(now)
        gap = any(b - a >= timedelta(hours=6) for a, b in zip(instants, instants[1:], strict=False))
        if not closed and not local_proof and (expired or gap):
            _coverage(db, flow, now)
        if not closed:
            if local_proof and (expired or row[3] is not None):
                closed = True
            elif expired and audit_id is not None:
                if (
                    row[6] is None
                    or datetime.fromisoformat(row[6]) < datetime.fromisoformat(row[2])
                    or row[7] is not None
                    or _held(db, flow)
                    or _uncertain_children(db, flow)
                ):
                    raise LifecycleError("closure evidence is incomplete or held")
                closed = True
            if closed:
                successful = (
                    db.execute(
                        "SELECT 1 FROM link_result WHERE flow_id = ? UNION ALL SELECT 1 "
                        "FROM link_success_observation WHERE flow_id = ? AND additional_slots "
                        "> 0 LIMIT 1",
                        (flow, flow),
                    ).fetchone()
                    is not None
                )
                final_state = (
                    "URL_MINTED"
                    if successful
                    else ("ABANDONED" if row[3] is not None else "URL_EXPIRED")
                )
                db.execute(
                    "UPDATE link_request SET state = ?, polling_closed_at = ?, "
                    "closure_audit_id = ? "
                    "WHERE flow_id = ?",
                    (
                        final_state,
                        _stamp(now),
                        audit_id,
                        flow,
                    ),
                )
        reaped = row[5] is not None
        if closed and not _held(db, flow) and not _uncertain_children(db, flow):
            deadlines = db.execute(
                "SELECT session_retention_expires_at FROM link_result WHERE flow_id = ? "
                "AND state IN ('TOKEN_EXPIRED', 'EXCHANGE_UNCERTAIN')",
                (flow,),
            ).fetchall()
            if all(r[0] is not None and now >= datetime.fromisoformat(r[0]) for r in deadlines):
                # Deterministic kind/name only, independent of legacy scalar refs.
                # Re-delete even if stamps exist: repairs the material-only state.
                store.delete(secret_ref_for(SecretKind.LINK_TOKEN, flow))
                db.execute(
                    "UPDATE link_request SET secret_ref = NULL, material_reaped_at = "
                    "coalesce(material_reaped_at, ?), "
                    "secret_ref_cleared_at = coalesce(secret_ref_cleared_at, ?) WHERE flow_id = ?",
                    (_stamp(now), _stamp(now), flow),
                )
                reaped = True
        db.commit()
        return closed, reaped
    except BaseException:
        db.rollback()
        raise


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    worker: WorkerOutcome
    closed: bool = False
    reaped: bool = False


def run_lifecycle(
    db: sqlite3.Connection,
    store: TokenStore,
    client: LinkClient,
    *,
    flow_id: str,
    country_codes: Sequence[str],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> LifecycleOutcome:
    if db.in_transaction:
        raise LifecycleError("lifecycle requires no active transaction")
    try:
        with exclusive_file_lock(request_lock_path(db, flow_id), blocking=False):
            outcome = _run_locked(db, store, client, flow_id, country_codes, clock)
            closed, reaped = _finish_locked(db, store, flow_id, clock())
            if _held(db, flow_id) and not outcome.held:
                outcome = WorkerOutcome(
                    outcome.busy, outcome.polled, True, outcome.finalized, outcome.failed
                )
            return LifecycleOutcome(outcome, closed, reaped)
    except LockUnavailable:
        return LifecycleOutcome(WorkerOutcome(busy=True))
    except Exception:
        db.rollback()
        raise LifecycleError(
            "Link lifecycle failed; retained evidence requires reinspection"
        ) from None


def request_abandon(db: sqlite3.Connection, *, flow_id: str, now: datetime) -> None:
    if db.in_transaction:
        raise LifecycleError("abandon requires no active transaction")
    with exclusive_file_lock(request_lock_path(db, flow_id)), db:
        if (
            db.execute(
                "UPDATE link_request SET abandon_requested_at = coalesce(abandon_requested_at, ?) "
                "WHERE flow_id = ?",
                (_stamp(now), flow_id),
            ).rowcount
            != 1
        ):
            raise LifecycleError("request is not recorded")


def adjudicate_closure(
    db: sqlite3.Connection,
    store: TokenStore,
    *,
    flow_id: str,
    audit_id: str,
    now: datetime,
    reviewed: bool,
) -> tuple[bool, bool]:
    if reviewed is not True or db.in_transaction:
        raise LifecycleError("explicit closure review with polling quiesced is required")
    secret_ref_for(SecretKind.LINK_TOKEN, audit_id)
    with exclusive_file_lock(request_lock_path(db, flow_id)):
        return _finish_locked(db, store, flow_id, now, audit_id=audit_id)
