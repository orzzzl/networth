"""Commit an already-attributed durable credential and its Item together (07a).

This is the post-storage primitive, not the automatic Link worker. Its caller
must capture exchange identifiers before storing material, reconcile *all*
applicable legacy/result names and persist any attribution/durability hold before
calling again. This primitive never decides that absent material permits an
exchange, never clears a hold, and has no exchange-capable client interface.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from networth.plaid.client import ItemInstitution
from networth.tokenstore import SecretKind, TokenStore, parse_secret_ref


class FinalizationError(RuntimeError):
    """A redacted refusal; the orchestration layer must retain the result."""


class MetadataClient(Protocol):
    def item_institution(
        self, access_token: str, *, expected_item_id: str, country_codes: Sequence[str]
    ) -> ItemInstitution: ...


def finalize_durable_result(
    connection: sqlite3.Connection,
    store: TokenStore,
    client: MetadataClient,
    *,
    result_id: str,
    secret_ref: str,
    country_codes: Sequence[str],
    now: datetime,
) -> int:
    """Finalize one attributed result; return its local Item row id.

    ``secret_ref`` is an explicit attribution from the caller, not a lookup
    preference. It may name the result UUID or its uniquely mapped legacy flow.
    Reconcile the selected name through TokenStore's durability barrier on each
    call, including restarts; absent or unverified material is an error. The
    caller remains responsible for checking other candidates and durable holds.

    Metadata errors leave EXCHANGING and material untouched. Existing Items retain
    their credential and health; another result for that Item retains its own
    credential reference. Network reads precede the short SQLite transaction.
    No health or freshness is inferred from those metadata reads.
    """
    if connection.in_transaction:
        raise FinalizationError("finalization requires a connection without an active transaction")
    if now.tzinfo is None or now.utcoffset() is None:
        raise FinalizationError("finalization requires an aware timestamp")
    stamp = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
    row = connection.execute(
        "SELECT flow_id, legacy_link_flow_id, state, item_id, secret_ref "
        "FROM link_result WHERE result_id = ?",
        (result_id,),
    ).fetchone()
    if row is None or row[2] not in ("EXCHANGING", "EXCHANGED"):
        raise FinalizationError("result is not ready for durable finalization")
    flow_id, legacy_id, state, captured_item_id, captured_ref = row
    kind, material_flow_id = parse_secret_ref(secret_ref)
    allowed = material_flow_id == result_id
    if not allowed and legacy_id is not None and material_flow_id == flow_id:
        allowed = (
            connection.execute(
                "SELECT 1 FROM link_request q JOIN link_flow f "
                "ON f.id = q.legacy_link_flow_id AND f.flow_id = q.flow_id "
                "WHERE q.flow_id = ? AND q.legacy_link_flow_id = ?",
                (flow_id, legacy_id),
            ).fetchone()
            is not None
        )
    if kind is not SecretKind.ACCESS_TOKEN or not allowed:
        raise FinalizationError("credential is not attributed to this result")
    if captured_ref is not None and captured_ref != secret_ref:
        raise FinalizationError("result already records a different credential")

    record = store.reconcile(material_flow_id)
    if record is None or record.item_id is None:
        raise FinalizationError("attributed durable material with Item identity is unavailable")
    material = store.get(record.secret_ref)
    item_id = record.item_id
    if item_id == material.reveal() or (
        captured_item_id is not None and captured_item_id != item_id
    ):
        raise FinalizationError("credential and captured Item identity disagree")
    if state == "EXCHANGED":
        item = connection.execute(
            "SELECT id FROM item WHERE plaid_item_id = ?", (item_id,)
        ).fetchone()
        # Non-NULL identity mismatches were refused above; these comparisons
        # also require both identities to have been captured before idempotent success.
        if item is None or captured_item_id != item_id or captured_ref != secret_ref:
            raise FinalizationError("exchanged result is missing its committed Item or reference")
        return int(item[0])

    metadata = client.item_institution(
        material.reveal(), expected_item_id=item_id, country_codes=country_codes
    )
    if (
        metadata.item_id != item_id
        or not metadata.institution_id
        or not metadata.name
        or material.reveal() in (metadata.item_id, metadata.institution_id, metadata.name)
        or type(metadata.is_oauth) is not bool
    ):
        raise FinalizationError("metadata is not attributable to the durable Item")

    try:
        connection.execute("BEGIN IMMEDIATE")
        # Recheck after the network calls: another finalizer may have committed,
        # or an adjudicator may have changed attribution while this call waited.
        current = connection.execute(
            "SELECT flow_id, legacy_link_flow_id, state, item_id, secret_ref "
            "FROM link_result WHERE result_id = ?",
            (result_id,),
        ).fetchone()
        if current is None or tuple(current) != tuple(row):
            raise FinalizationError("result changed during metadata retrieval; reread before retry")
        existing = connection.execute(
            "SELECT i.id, n.plaid_institution_id FROM item i "
            "JOIN institution n ON n.id = i.institution_id WHERE i.plaid_item_id = ?",
            (item_id,),
        ).fetchone()
        if existing is not None and existing[1] != metadata.institution_id:
            raise FinalizationError("stored Item and retrieved institution identity disagree")
        connection.execute(
            "INSERT INTO institution(plaid_institution_id, name, is_oauth) VALUES (?, ?, ?) "
            "ON CONFLICT(plaid_institution_id) DO UPDATE SET name = excluded.name, "
            "is_oauth = excluded.is_oauth",
            (metadata.institution_id, metadata.name, int(metadata.is_oauth)),
        )
        institution = connection.execute(
            "SELECT id FROM institution WHERE plaid_institution_id = ?",
            (metadata.institution_id,),
        ).fetchone()
        assert institution is not None
        if existing is None:
            cursor = connection.execute(
                "INSERT INTO item(institution_id, plaid_item_id, secret_ref, status, "
                "status_since, created_at) VALUES (?, ?, ?, 'DEGRADED', ?, ?)",
                (institution[0], item_id, secret_ref, stamp, stamp),
            )
            assert cursor.lastrowid is not None
            local_item_id = cursor.lastrowid
        else:
            local_item_id = int(existing[0])
        connection.execute(
            "UPDATE link_result SET state = 'EXCHANGED', item_id = ?, secret_ref = ? "
            "WHERE result_id = ? AND state = 'EXCHANGING'",
            (item_id, secret_ref, result_id),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise FinalizationError(
            "Item finalization transaction failed; durable material retained"
        ) from None
    except BaseException:
        connection.rollback()
        raise
    return local_item_id
