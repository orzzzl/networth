"""Durably ingest Link polling evidence; never exchange or infer cleanup (07a).

One open observation groups unidentified successes for a request/session/reason.
It is an unresolved *set*, not one spent slot. Repeated polls update its count
bound; only an operator can adjudicate the additional cost of that whole set.
After adjudication, further success evidence reopens the hold, even if it looks
identical. Past decisions remain in the audit ledger; a new review replaces the
aggregate additional-slot count, so recovered identities are not counted twice.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from networth.plaid.client import LinkSessionPoll, LinkSessionRecord
from networth.tokenstore import (
    Secret,
    SecretKind,
    TokenStore,
    TokenStoreError,
    new_flow_id,
    secret_ref_for,
)


class ObservationError(RuntimeError):
    """Redacted refusal that never contains reply or credential contents."""


@dataclass(frozen=True, slots=True)
class ObservedToken:
    result_id: str
    public_token: Secret


@dataclass(frozen=True, slots=True)
class IngestedPoll:
    tokens: tuple[ObservedToken, ...]
    observation_ids: tuple[str, ...]


def _stamp(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ObservationError("observation timestamps must be timezone aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _optional_stamp(value: datetime | None) -> str | None:
    return None if value is None else _stamp(value)


def _hold(
    db: sqlite3.Connection, flow: str, session: str | None, reason: str, count: int, stamp: str
) -> str:
    row = db.execute(
        "SELECT observation_id FROM link_success_observation "
        "WHERE flow_id = ? AND link_session_id IS ? AND reason = ? "
        "ORDER BY resolved_at IS NOT NULL, observed_at LIMIT 1",
        (flow, session, reason),
    ).fetchone()
    if row is not None:
        identity = str(row[0])
        db.execute(
            "UPDATE link_success_observation SET max_reported_results = "
            "max(coalesce(max_reported_results, 0), ?), last_observed_at = ?, "
            "resolved_at = NULL, resolution_note = NULL, additional_slots = NULL "
            "WHERE observation_id = ?",
            (count, stamp, identity),
        )
        return identity
    identity = new_flow_id()
    db.execute(
        "INSERT INTO link_success_observation(observation_id, flow_id, link_session_id, "
        "reason, observed_at, max_reported_results, last_observed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (identity, flow, session, reason, stamp, count, stamp),
    )
    return identity


def _session(db: sqlite3.Connection, flow: str, session: LinkSessionRecord) -> None:
    started = _optional_stamp(session.started_at)
    finished = _optional_stamp(session.finished_at)
    old = db.execute(
        "SELECT started_at, finished_at FROM link_session "
        "WHERE flow_id = ? AND link_session_id = ?",
        (flow, session.session_id),
    ).fetchone()
    if old is not None:
        for previous, observed in zip(old, (started, finished), strict=True):
            if (
                previous is not None
                and observed is not None
                and datetime.fromisoformat(previous) != datetime.fromisoformat(observed)
            ):
                raise ObservationError("session timestamps changed; retain evidence for review")
    # An exit observed earlier cannot erase a success observed in another reply.
    success = (
        bool(session.item_add_results or session.public_tokens)
        or db.execute(
            "SELECT 1 FROM link_result WHERE flow_id = ? AND link_session_id = ? "
            "UNION ALL SELECT 1 FROM link_success_observation WHERE flow_id = ? "
            "AND link_session_id = ? LIMIT 1",
            (flow, session.session_id, flow, session.session_id),
        ).fetchone()
        is not None
    )
    state = "SESSION_EXITED" if finished is not None and not success else "SESSION_STARTED"
    db.execute(
        "INSERT INTO link_session(flow_id, link_session_id, state, started_at, finished_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(flow_id, link_session_id) DO UPDATE SET "
        "state = CASE WHEN excluded.state = 'SESSION_STARTED' AND NOT ? "
        "THEN link_session.state ELSE excluded.state END, "
        "started_at = coalesce(link_session.started_at, excluded.started_at), "
        "finished_at = coalesce(link_session.finished_at, excluded.finished_at)",
        (flow, session.session_id, state, started, finished, success),
    )


def ingest_poll(
    db: sqlite3.Connection, store: TokenStore, *, flow_id: str, poll: LinkSessionPoll, now: datetime
) -> IngestedPoll:
    """Commit every result identity before returning transient exchange inputs.

    BEGIN IMMEDIATE serializes ingestion/adjudication in this database only.
    The future reaper must use the same database lock when deleting the digest
    key. No public token, plain token digest, account or institution is stored.
    This function never changes request terminal state or polling closure, and
    does not call Plaid. Its output is *not* authorization to exchange: the
    worker must reconcile material/holds, check deadlines and claim each result.
    """
    if db.in_transaction:
        raise ObservationError("ingestion requires no active transaction")
    stamp = _stamp(now)
    ref = secret_ref_for(SecretKind.LINK_TOKEN, flow_id)
    tokens: dict[str, ObservedToken] = {}
    holds: set[str] = set()
    try:
        db.execute("BEGIN IMMEDIATE")
        request = db.execute(
            "SELECT secret_ref, material_reaped_at FROM link_request WHERE flow_id = ?", (flow_id,)
        ).fetchone()
        if request is None:
            raise ObservationError("request is not recorded")
        key: Secret | None = None
        if request[0] == ref and request[1] is None:
            # Unreadable/unverified is not a usable deduplication key.
            with contextlib.suppress(TokenStoreError, OSError):
                key = store.get(ref)
        legacy = (
            db.execute(
                "SELECT 1 FROM link_result WHERE flow_id = ? AND token_digest IS NULL LIMIT 1",
                (flow_id,),
            ).fetchone()
            is not None
        )
        # Validate the complete typed reply before writing any provider identifier.
        bearers = set(poll.public_tokens)
        if key is not None:
            bearers.add(key.reveal())
        for session in poll.sessions:
            if session.session_id is not None and (
                not isinstance(session.session_id, str)
                or not session.session_id.strip()
                or session.session_id in bearers
            ):
                raise ObservationError("session identity is unusable")
            if type(session.item_add_results) is not int or session.item_add_results < 0:
                raise ObservationError("result count is unusable")
            if any(not isinstance(token, str) or not token for token in session.public_tokens):
                raise ObservationError("public token is unusable")
            _optional_stamp(session.started_at)
            _optional_stamp(session.finished_at)
        if any(s.item_add_results or s.public_tokens for s in poll.sessions):
            reopened = db.execute(
                "SELECT observation_id FROM link_success_observation "
                "WHERE flow_id = ? AND resolved_at IS NOT NULL",
                (flow_id,),
            ).fetchall()
            db.execute(
                "UPDATE link_success_observation SET resolved_at = NULL, "
                "resolution_note = NULL, additional_slots = NULL "
                "WHERE flow_id = ? AND resolved_at IS NOT NULL",
                (flow_id,),
            )
            holds.update(str(row[0]) for row in reopened)
        # Aggregate missing-session entries as one unidentified set, not one identity
        # per position in the response (which changes when replies are reordered).
        missing_session_count = sum(
            max(s.item_add_results, len(s.public_tokens))
            for s in poll.sessions
            if s.session_id is None
        )
        if missing_session_count:
            holds.add(_hold(db, flow_id, None, "MISSING_SESSION_ID", missing_session_count, stamp))
        for session in poll.sessions:
            sid = session.session_id
            if sid is None:
                continue
            _session(db, flow_id, session)
            # Use the session's persisted finish, so an incomplete later reply does
            # not erase the clock, and a finish first seen later fills older results.
            finished_text = db.execute(
                "SELECT finished_at FROM link_session WHERE flow_id = ? AND link_session_id = ?",
                (flow_id, sid),
            ).fetchone()[0]
            finished = None if finished_text is None else datetime.fromisoformat(finished_text)
            expires = None if finished is None else _stamp(finished + timedelta(minutes=30))
            retention = None if finished is None else _stamp(finished + timedelta(hours=6))
            db.execute(
                "UPDATE link_result SET finished_at = coalesce(finished_at, ?), "
                "token_exchange_expires_at = coalesce(token_exchange_expires_at, ?), "
                "session_retention_expires_at = coalesce(session_retention_expires_at, ?) "
                "WHERE flow_id = ? AND link_session_id = ?",
                (finished_text, expires, retention, flow_id, sid),
            )
            if session.tokens_missing:
                holds.add(
                    _hold(db, flow_id, sid, "MISSING_PUBLIC_TOKEN", session.tokens_missing, stamp)
                )
            if not session.public_tokens:
                continue
            if key is None or legacy:
                reason = "DIGEST_KEY_UNAVAILABLE" if key is None else "LEGACY_ATTRIBUTION_AMBIGUOUS"
                holds.add(_hold(db, flow_id, sid, reason, len(set(session.public_tokens)), stamp))
                continue
            for token in session.public_tokens:
                digest = hmac.new(
                    key.reveal().encode(),
                    b"networth/link-result/v1\0" + token.encode(),
                    hashlib.sha256,
                ).hexdigest()
                existing = db.execute(
                    "SELECT result_id, link_session_id FROM link_result "
                    "WHERE flow_id = ? AND token_digest = ?",
                    (flow_id, digest),
                ).fetchone()
                if existing is not None and existing[1] != sid:
                    raise ObservationError(
                        "token was attributed to a different session; review required"
                    )
                rid = new_flow_id() if existing is None else str(existing[0])
                if existing is None:
                    db.execute(
                        "INSERT INTO link_result(result_id, flow_id, link_session_id, "
                        "token_digest, state, finished_at, token_exchange_expires_at, "
                        "session_retention_expires_at) "
                        "VALUES (?, ?, ?, ?, 'SUCCESS_PENDING_EXCHANGE', ?, ?, ?)",
                        (rid, flow_id, sid, digest, finished_text, expires, retention),
                    )
                tokens[rid] = ObservedToken(rid, Secret(token))
        db.execute(
            "UPDATE link_request SET last_poll_at = ?, poll_error = NULL WHERE flow_id = ?",
            (stamp, flow_id),
        )
        db.commit()
    except sqlite3.Error:
        db.rollback()
        raise ObservationError("poll evidence transaction failed; no exchange authorized") from None
    except BaseException:
        db.rollback()
        raise
    return IngestedPoll(tuple(tokens.values()), tuple(sorted(holds)))


def adjudicate_observation(
    db: sqlite3.Connection,
    *,
    observation_id: str,
    additional_slots: int,
    audit_id: str,
    now: datetime,
    reviewed: bool,
) -> None:
    """Record an operator's reviewed slot outcome, never an automatic inference.

    audit_id names private evidence by a minted UUID; free-form evidence and
    credentials stay outside SQLite. This resolves only this observation, never
    other holds, result states or credentials. A subsequent successful poll reopens
    the aggregate hold; the audit ledger retains every earlier decision. The caller
    must quiesce polling and review the whole unresolved
    set, including overlap with Items/results and other observations.
    """
    if db.in_transaction:
        raise ObservationError("adjudication requires no active transaction")
    if reviewed is not True:
        raise ObservationError("explicit reviewed slot adjudication is required")
    secret_ref_for(SecretKind.LINK_TOKEN, observation_id)
    secret_ref_for(SecretKind.LINK_TOKEN, audit_id)
    if type(additional_slots) is not int or additional_slots < 0:
        raise ObservationError("additional slots must be a nonnegative integer")
    stamp = _stamp(now)
    try:
        db.execute("BEGIN IMMEDIATE")
        # Preserve pre-ledger decisions from schema 6 before any new adjudication.
        # The migration seeded those records; every decision here appends exactly
        # once in the same transaction as updating its current projection.
        changed = db.execute(
            "UPDATE link_success_observation SET resolved_at = ?, resolution_note = ?, "
            "additional_slots = ? WHERE observation_id = ? AND resolved_at IS NULL",
            (stamp, "Reviewed private evidence " + audit_id, additional_slots, observation_id),
        ).rowcount
        if changed != 1:
            raise ObservationError("observation is missing or already adjudicated")
        db.execute(
            "INSERT INTO link_observation_adjudication(observation_id, resolved_at, "
            "resolution_note, additional_slots) VALUES (?, ?, ?, ?)",
            (observation_id, stamp, "Reviewed private evidence " + audit_id, additional_slots),
        )
        db.commit()
    except sqlite3.Error:
        db.rollback()
        raise ObservationError("adjudication transaction failed") from None
    except BaseException:
        db.rollback()
        raise
