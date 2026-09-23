"""Inspect every request credential candidate before authorizing worker decisions.

The returned attribution is a snapshot, not permission to exchange. The worker
must retain its request lock across reconciliation, conditional claim, send and
storage. This component neither sends nor classifies stale claims. In particular,
local absence says nothing about whether an earlier send succeeded remotely.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from networth.tokenstore import (
    SecretKind,
    SecretRecord,
    TokenStore,
    TokenStoreError,
    new_flow_id,
    parse_secret_ref,
    secret_ref_for,
)


class ReconciliationError(RuntimeError):
    """Redacted failure; no automatic exchange is authorized."""


@dataclass(frozen=True, slots=True, repr=False)
class ResultMaterial:
    result_id: str
    item_id: str
    secret_refs: tuple[str, ...]

    def __repr__(self) -> str:
        return "ResultMaterial(<redacted>)"


@dataclass(frozen=True, slots=True)
class ReconciledRequest:
    materials: tuple[ResultMaterial, ...]
    absent_result_ids: tuple[str, ...]
    hold_ids: tuple[str, ...]


def _stamp(now: datetime) -> str:
    if now.utcoffset() is None:
        raise ReconciliationError("reconciliation requires an aware timestamp")
    return now.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _open_holds(db: sqlite3.Connection, flow_id: str) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in db.execute(
            "SELECT hold_id FROM link_material_hold WHERE flow_id = ? "
            "AND resolved_at IS NULL ORDER BY hold_id",
            (flow_id,),
        )
    )


def _save_holds(
    db: sqlite3.Connection, flow_id: str, reasons: set[str], stamp: str
) -> tuple[str, ...]:
    for reason in sorted(reasons):
        db.execute(
            "INSERT INTO link_material_hold(hold_id, flow_id, reason, observed_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (new_flow_id(), flow_id, reason, stamp),
        )
    return _open_holds(db, flow_id)


def reconcile_request(
    db: sqlite3.Connection, store: TokenStore, *, flow_id: str, now: datetime
) -> ReconciledRequest:
    """Reconcile result, original-flow and recorded access-token names together.

    All candidates pass through TokenStore.reconcile, including its final/pending
    lookup and durability barrier. No first-found preference can hide another
    candidate. A hold blocks the entire request and survives restart even when
    the bad file later disappears or becomes readable. SQLite serializes the
    evidence/hold transaction; it does not serialize a caller's later exchange.
    """
    if db.in_transaction:
        raise ReconciliationError("reconciliation requires no active transaction")
    secret_ref_for(SecretKind.LINK_TOKEN, flow_id)
    stamp = _stamp(now)
    try:
        db.execute("BEGIN IMMEDIATE")
        request = db.execute(
            "SELECT legacy_link_flow_id FROM link_request WHERE flow_id = ?", (flow_id,)
        ).fetchone()
        if request is None:
            raise ReconciliationError("request is not recorded")
        holds = _open_holds(db, flow_id)
        if holds:
            db.commit()
            return ReconciledRequest((), (), holds)
        rows = db.execute(
            "SELECT result_id, legacy_link_flow_id, item_id, secret_ref "
            "FROM link_result WHERE flow_id = ? ORDER BY result_id",
            (flow_id,),
        ).fetchall()
        reasons: set[str] = set()
        candidates = {flow_id}
        # A candidate's name has one owner only. A recorded arbitrary reference
        # is inspected but cannot establish attribution merely by being recorded.
        owners: dict[str, str] = {}
        expected: dict[str, str | None] = {}
        recorded: dict[str, str | None] = {}
        for rid, legacy_id, item_id, ref in rows:
            candidates.add(rid)
            owners[rid] = rid
            expected[rid] = item_id
            recorded[rid] = ref
            if legacy_id is not None:
                legacy = db.execute(
                    "SELECT flow_id, secret_ref FROM link_flow WHERE id = ?", (legacy_id,)
                ).fetchone()
                if (
                    legacy is None
                    or legacy[0] != flow_id
                    or request[0] != legacy_id
                    or (flow_id in owners and owners[flow_id] != rid)
                ):
                    reasons.add("ATTRIBUTION_AMBIGUOUS")
                else:
                    owners[flow_id] = rid
                if legacy is not None and legacy[1] is not None:
                    _recorded_candidate(
                        legacy[1], candidates, reasons, allowed_ids={flow_id}, allow_link=True
                    )
            if ref is not None:
                _recorded_candidate(
                    ref,
                    candidates,
                    reasons,
                    allowed_ids={rid, flow_id} if owners.get(flow_id) == rid else {rid},
                    allow_link=False,
                )
        # A request's legacy reference is still relevant if its mapped result is
        # missing. Such material must be found and held, never treated as absent.
        if request[0] is not None:
            legacy = db.execute(
                "SELECT flow_id, secret_ref FROM link_flow WHERE id = ?", (request[0],)
            ).fetchone()
            if legacy is None or legacy[0] != flow_id:
                reasons.add("ATTRIBUTION_AMBIGUOUS")
            elif legacy[1] is not None:
                _recorded_candidate(
                    legacy[1], candidates, reasons, allowed_ids={flow_id}, allow_link=True
                )
        found: dict[str, list[SecretRecord]] = {}
        for candidate in sorted(candidates):
            try:
                record = store.reconcile(candidate)
                if record is None:
                    continue
                material = store.get(record.secret_ref)
            except (TokenStoreError, OSError):
                reasons.add("UNVERIFIED_MATERIAL")
                continue
            owner = owners.get(candidate)
            if (
                owner is None
                or record.secret_ref != secret_ref_for(SecretKind.ACCESS_TOKEN, candidate)
                or record.item_id is None
                or record.item_id == material.reveal()
                or (expected[owner] is not None and expected[owner] != record.item_id)
            ):
                reasons.add("ATTRIBUTION_AMBIGUOUS")
                continue
            found.setdefault(owner, []).append(record)
        materials: list[ResultMaterial] = []
        absent: list[str] = []
        for rid in expected:
            records = found.get(rid, [])
            identities = {record.item_id for record in records}
            refs = tuple(sorted(record.secret_ref for record in records))
            if len(identities) > 1 or (recorded[rid] is not None and recorded[rid] not in refs):
                reasons.add("ATTRIBUTION_AMBIGUOUS")
            elif records:
                identity = records[0].item_id
                assert identity is not None
                materials.append(ResultMaterial(rid, identity, refs))
            else:
                absent.append(rid)
        holds = _save_holds(db, flow_id, reasons, stamp)
        db.commit()
    except sqlite3.Error:
        db.rollback()
        raise ReconciliationError("credential reconciliation transaction failed") from None
    except BaseException:
        db.rollback()
        raise
    if holds:
        return ReconciledRequest((), (), holds)
    return ReconciledRequest(tuple(materials), tuple(absent), ())


def _recorded_candidate(
    ref: str, candidates: set[str], reasons: set[str], *, allowed_ids: set[str], allow_link: bool
) -> None:
    try:
        kind, identity = parse_secret_ref(ref)
    except TokenStoreError:
        reasons.add("ATTRIBUTION_AMBIGUOUS")
        return
    if identity not in allowed_ids:
        reasons.add("ATTRIBUTION_AMBIGUOUS")
    if kind is SecretKind.ACCESS_TOKEN:
        candidates.add(identity)
    elif not allow_link:
        reasons.add("ATTRIBUTION_AMBIGUOUS")


def adjudicate_material_hold(
    db: sqlite3.Connection, *, hold_id: str, audit_id: str, now: datetime, reviewed: bool
) -> None:
    """Record permission to inspect material again, not a found/absent verdict.

    The authorized local operator must quiesce workers, establish attribution and
    account for every possible spent slot before confirming. This does not clear
    success observations, invent Item identity, change states, delete credentials,
    or authorize retry of an uncertain exchange. Reinspection can create a new
    hold; the old record remains immutable audit history.
    """
    if db.in_transaction:
        raise ReconciliationError("adjudication requires no active transaction")
    if reviewed is not True:
        raise ReconciliationError("explicit reviewed material adjudication is required")
    secret_ref_for(SecretKind.LINK_TOKEN, hold_id)
    secret_ref_for(SecretKind.LINK_TOKEN, audit_id)
    stamp = _stamp(now)
    try:
        db.execute("BEGIN IMMEDIATE")
        changed = db.execute(
            "UPDATE link_material_hold SET resolved_at = ?, audit_id = ? "
            "WHERE hold_id = ? AND resolved_at IS NULL",
            (stamp, audit_id, hold_id),
        ).rowcount
        if changed != 1:
            raise ReconciliationError("hold is missing or already adjudicated")
        db.commit()
    except sqlite3.Error:
        db.rollback()
        raise ReconciliationError("material adjudication transaction failed") from None
    except BaseException:
        db.rollback()
        raise
