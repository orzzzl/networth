"""Host-local read diagnostics. Never repair rows, reap files or contact a provider."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from networth.backup.state import BackupStateStore
from networth.item_budget import ItemBudgetError, read_item_budget
from networth.model.figure import require_utc
from networth.query import NetWorthQuery, NetWorthQueryError
from networth.store import Store
from networth.tokenstore import (
    SecretKind,
    TokenStoreError,
    inspect_presence,
    secret_ref_for,
)


def rows(db: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, Any]]:
    cursor = db.execute(sql, params)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


def age(value: datetime | str | None, now: datetime) -> dict[str, object]:
    instant = datetime.fromisoformat(value) if isinstance(value, str) else value
    if instant is None:
        return {"at": None, "age_seconds": None, "status": "NEVER_RECORDED"}
    require_utc(instant, field="diagnostic timestamp")
    seconds = int((now - instant).total_seconds())
    return {
        "at": instant,
        "age_seconds": seconds,
        "status": "FUTURE_CLOCK" if seconds < 0 else "RECORDED",
    }


def identifier(value: object) -> dict[str, object]:
    return {"status": "never observed" if value is None else "present", "value": value}


def presence(tokens: Path, ref: str | None) -> dict[str, str]:
    if ref is None:
        return {"published": "NO_REFERENCE", "pending": "NO_REFERENCE"}
    try:
        return inspect_presence(tokens, ref)
    except TokenStoreError:
        return {"published": "INVALID_REFERENCE", "pending": "INVALID_REFERENCE"}


def _reap_status(db: sqlite3.Connection, flow: dict[str, Any], now: datetime) -> dict[str, object]:
    """Describe already-closed requests; never infer closure from URL expiry.

    Mirrors the final deletion preconditions in link_lifecycle, without running
    its reconciliation/writes. Unknown child clocks or unresolved holds retain
    material even when an unrelated URL clock has expired.
    """
    closed = flow["polling_closed_at"]
    if closed is None:
        return {"status": "POLLING_NOT_CLOSED", "reap_after": None}
    flow_id = flow["flow_id"]
    blocked = db.execute(
        "SELECT 1 FROM link_material_hold WHERE flow_id=? AND resolved_at IS NULL "
        "UNION ALL SELECT 1 FROM link_success_observation WHERE flow_id=? AND resolved_at IS NULL "
        "UNION ALL SELECT 1 FROM link_session WHERE flow_id=? AND finished_at IS NULL "
        "UNION ALL SELECT 1 FROM link_result WHERE flow_id=? AND "
        "(state IN ('SUCCESS_PENDING_EXCHANGE','EXCHANGING') OR finished_at IS NULL) LIMIT 1",
        (flow_id, flow_id, flow_id, flow_id),
    ).fetchone()
    deadlines = [
        row[0]
        for row in db.execute(
            "SELECT session_retention_expires_at FROM link_result WHERE flow_id=? "
            "AND state IN ('TOKEN_EXPIRED','EXCHANGE_UNCERTAIN')",
            (flow_id,),
        )
    ]
    if blocked or any(value is None for value in deadlines):
        return {"status": "HELD_OR_UNKNOWN_DEADLINE", "reap_after": None}
    deadline = max(datetime.fromisoformat(value) for value in [closed, *deadlines])
    require_utc(deadline, field="reap deadline")
    return {"status": "DUE" if now >= deadline else "RETAIN_UNTIL_DEADLINE", "reap_after": deadline}


def _links(db: sqlite3.Connection, tokens: Path, now: datetime) -> list[dict[str, Any]]:
    reports = []
    for flow in rows(
        db,
        "SELECT flow_id, state, secret_ref, polling_closed_at, material_reaped_at "
        "FROM link_request ORDER BY flow_id",
    ):
        flow_id = flow["flow_id"]
        results = rows(
            db,
            "SELECT result_id, state, link_session_id, item_id, "
            "token_exchange_expires_at, session_retention_expires_at, "
            "exchange_attempts FROM link_result WHERE flow_id=? ORDER BY result_id",
            (flow_id,),
        )
        for result in results:
            result["attempts"] = [
                {"attempt_number": row[0], "request_id": identifier(row[1])}
                for row in db.execute(
                    "SELECT attempt_number, request_id FROM link_result_attempt "
                    "WHERE result_id=? ORDER BY attempt_number",
                    (result["result_id"],),
                )
            ]
            for field in ("link_session_id", "item_id"):
                result[field] = identifier(result[field])
        sessions = rows(
            db,
            "SELECT link_session_id, state, started_at, finished_at "
            "FROM link_session WHERE flow_id=? ORDER BY link_session_id",
            (flow_id,),
        )
        # Inspect the deterministic name even after the row reference was cleared:
        # a surviving pending file is still material, not a successful reap.
        try:
            deterministic = secret_ref_for(SecretKind.LINK_TOKEN, flow_id)
        except TokenStoreError:
            deterministic = None
        material = presence(tokens, deterministic)
        reaping = _reap_status(db, flow, now)
        reaping["overdue_material"] = reaping["status"] == "DUE" and "PRESENT" in material.values()
        reports.append(
            {
                "flow_id": identifier(flow_id),
                "state": flow["state"],
                "link_session_ids": [identifier(s["link_session_id"]) for s in sessions]
                or [identifier(None)],
                "results": results,
                "sessions": sessions,
                "item_id": identifier(None)
                if not results
                else "See each result; no inferred identity.",
                "request_ids": "never observed"
                if not any(r["attempts"] for r in results)
                else "See each attempt; missing responses remain never observed.",
                "material_presence": material,
                "reference_presence": presence(tokens, flow["secret_ref"]),
                "dangling_secret_ref": flow["secret_ref"] is not None
                and presence(tokens, flow["secret_ref"])["published"] == "ABSENT",
                "reaping": reaping,
                "observations": rows(
                    db,
                    "SELECT observation_id, link_session_id, reason, resolved_at "
                    "FROM link_success_observation WHERE flow_id=?",
                    (flow_id,),
                ),
                "holds": rows(
                    db,
                    "SELECT hold_id, reason, resolved_at FROM link_material_hold WHERE flow_id=?",
                    (flow_id,),
                ),
            }
        )
    # Imported legacy evidence can exist after migration; never lose its ticket.
    for flow in rows(
        db,
        "SELECT id, flow_id, state, link_session_id, item_id, secret_ref, "
        "token_exchange_expires_at, session_retention_expires_at, exchange_attempts "
        "FROM link_flow f WHERE NOT EXISTS "
        "(SELECT 1 FROM link_request r WHERE r.legacy_link_flow_id=f.id) ORDER BY id",
    ):
        reports.append(
            {
                "legacy": True,
                "flow_id": identifier(flow["flow_id"]),
                "state": flow["state"],
                "link_session_id": identifier(flow["link_session_id"]),
                "item_id": identifier(flow["item_id"]),
                "attempts": [
                    {"attempt_number": row[0], "request_id": identifier(row[1])}
                    for row in db.execute(
                        "SELECT attempt_number, request_id "
                        "FROM link_exchange_attempt WHERE link_flow_id=? "
                        "ORDER BY attempt_number",
                        (flow["id"],),
                    )
                ],
                "material_presence": presence(tokens, flow["secret_ref"]),
                "reaping": {"status": "LEGACY_REQUIRES_RECONCILIATION"},
                "token_exchange_expires_at": flow["token_exchange_expires_at"],
                "session_retention_expires_at": flow["session_retention_expires_at"],
                "exchange_attempts": flow["exchange_attempts"],
            }
        )
    return reports


def read_doctor(db: sqlite3.Connection, tokens: Path, now: datetime) -> tuple[object, int]:
    require_utc(now, field="now")
    status = 0
    try:
        latest = NetWorthQuery(Store(db)).latest()
        snapshot = "NO_SNAPSHOT" if latest is None else "CONSISTENT"
    except NetWorthQueryError:
        snapshot = (
            "MISMATCH: active population or values differ; a new successful snapshot is required"
        )
        status = 1
    try:
        count = read_item_budget(db)
        budget: dict[str, object] = asdict(count)
        budget["remaining"] = count.remaining
    except ItemBudgetError:
        budget = {"remaining": None, "status": "UNKNOWN: unresolved or contradictory Link evidence"}
        status = 1
    backup = BackupStateStore(db).status()
    restore_age = age(backup.last_verified_restore_at, now)
    seconds = restore_age["age_seconds"]
    restore_age["days_since"] = (
        seconds // 86400 if isinstance(seconds, int) and seconds >= 0 else None
    )
    publication = db.execute(
        "SELECT published_at FROM publication ORDER BY id DESC LIMIT 1"
    ).fetchone()
    items = rows(
        db,
        "SELECT id, status, status_since, last_successful_sync, last_attempted_sync, "
        "secret_ref FROM item ORDER BY id",
    )
    for item in items:
        item["credential_file_presence"] = presence(tokens, item.pop("secret_ref"))
    report = {
        "scope": "SYNC_HOST: local database and file presence only; no remote probes",
        "checked_at": now,
        "snapshot": snapshot,
        "accounts": rows(
            db,
            "SELECT id, name, freshness_policy, reconciliation_state, "
            "last_fetch_at, last_source_as_of, include_in_net_worth, archived_at "
            "FROM account ORDER BY id",
        ),
        "items": items,
        "item_budget": budget,
        "credential_presence_scope": "File presence only, not proof of usable tokens; filesystem "
        "observations are not atomic with the database snapshot.",
        "items_with_missing_token_files": sum(
            i["credential_file_presence"]["published"] == "ABSENT" for i in items
        ),
        "last_successful_backup": age(backup.last_successful_backup, now),
        "last_verified_restore": restore_age,
        "last_restore_attempt_failed": backup.last_verified_restore_error is not None,
        "key_escrow_confirmed_at": {
            "at": backup.key_escrow_confirmed_at,
            "evidence": "Owner's own attestation; not independently verified",
        },
        "last_successful_publication": age(None if publication is None else publication[0], now),
        "probe_refusal_count": backup.probe_refusal_count,
        "dispatch_rejection_count": backup.dispatch_rejection_count,
        "restore_lineage_diagnostic": rows(
            db,
            "SELECT publish_epoch, epoch_bumped_at, "
            "epoch_bumped_reason FROM daemon_state WHERE id=1",
        ),
        "link_flows": _links(db, tokens, now),
        "open_alerts": rows(
            db,
            "SELECT id, kind, item_id, account_id, created_at, acknowledged_at "
            "FROM alert WHERE resolved_at IS NULL ORDER BY id",
        ),
    }
    return report, status
