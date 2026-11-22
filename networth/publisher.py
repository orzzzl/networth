"""Build and atomically store the encrypted phone payload for task 19."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from networth.alerts import AlertEvaluator, DeliverableAlert
from networth.item_budget import ItemBudget, ItemBudgetError, read_item_budget
from networth.model import Snapshot
from networth.model.figure import require_utc
from networth.payload import KEY_BYTES, NONCE_BYTES, PayloadEnvelope, seal_payload
from networth.query import AccountRead, NetWorthQuery, NetWorthRead
from networth.store import Store

# Still "1" with `history` in the body: the field is additive, and an app that
# predates it reads named keys and ignores the rest, so it cannot misread a
# payload that carries one. A bump is for a change an older app would misread.
SCHEMA_VERSION = "1"
PUBLISH_INTERVAL_SECONDS = 86_400
GRACE_SECONDS = 21_600

#: The owner's zone. The phone shows every instant in it, so the series is
#: bucketed by its calendar days: a reading taken at 00:07Z on the 22nd is on
#: the 21st for the person looking at it, and that is the day it is drawn on.
OWNER_ZONE = ZoneInfo("America/Los_Angeles")

#: The longest range the phone offers (5Y). The series stops there so the
#: payload stays bounded: about 270 bytes a day, under half of what the phone
#: will read from one response even at five full years.
HISTORY_YEARS = 5

#: Exactly the fields the phone's `DatedTotal.fromJson` reads, so every point
#: passes the same parser as the headline and costs no more than it needs to.
_HISTORY_TOTAL_FIELDS = (
    "account_count",
    "age_state",
    "as_of",
    "assets_minor",
    "currency",
    "is_complete",
    "liabilities_minor",
    "static_account_count",
    "unknown_freshness_account_count",
    "value_minor",
)


class PublisherError(RuntimeError):
    """The current local state cannot produce one honest publication."""


class PublisherTransactionError(PublisherError):
    """Publisher could not own the complete atomic transaction."""


class PairingUnavailableError(PublisherError):
    """There is not exactly one active phone pairing to publish for."""


class PayloadKeyError(PublisherError):
    """The active pairing's key reference did not resolve to a 256-bit key."""


@dataclass(frozen=True, slots=True)
class Publication:
    """The committed metadata and envelope, without plaintext or key material."""

    id: int
    snapshot_id: int
    seq: int
    envelope: PayloadEnvelope

    def __post_init__(self) -> None:
        for field in ("id", "snapshot_id", "seq"):
            value = cast(int, getattr(self, field))
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{field} must be an integer")
            if value <= 0:
                raise ValueError(f"{field} must be positive")
        if not isinstance(self.envelope, PayloadEnvelope):
            raise TypeError("envelope must be a PayloadEnvelope")
        if self.envelope.seq != str(self.seq):
            raise ValueError("envelope seq must match publication seq")


def _timestamp(value: datetime) -> str:
    require_utc(value, field="timestamp")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _optional_timestamp(value: datetime | None) -> str | None:
    return None if value is None else _timestamp(value)


def _total(snapshot: Snapshot) -> dict[str, object]:
    return {
        "value_minor": snapshot.net_worth.value_minor,
        "assets_minor": snapshot.assets.value_minor,
        "liabilities_minor": snapshot.liabilities.value_minor,
        "currency": snapshot.net_worth.currency,
        # Keep the tag and nullable member adjacent under the total. Section 10
        # names these exact fields so a consumer must branch on age_state before
        # treating as_of as a date.
        "age_state": snapshot.age.state.value,
        "as_of": _optional_timestamp(snapshot.age.as_of),
        "oldest_known_source_as_of": _optional_timestamp(snapshot.age.oldest_known_source_as_of),
        "account_count": snapshot.counts.account_count,
        "stale_account_count": snapshot.counts.stale_account_count,
        "unknown_freshness_account_count": snapshot.counts.unknown_freshness_account_count,
        "static_account_count": snapshot.counts.static_account_count,
        "reauth_account_count": snapshot.counts.reauth_account_count,
        "unreconciled_account_count": snapshot.counts.unreconciled_account_count,
        "is_complete": snapshot.is_complete,
    }


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February, in a year that has none
        return day.replace(year=day.year - years, day=28)


def _history(snapshots: Sequence[Snapshot]) -> list[dict[str, object]]:
    """The curve's series: each day's latest stored snapshot, as stored.

    One point per owner-zone calendar day — the last snapshot taken that day,
    because `snapshots` is chronological and a later one replaces an earlier one.
    Nothing is recomputed or filled in: a day with no snapshot has no point.

    A snapshot over **zero accounts** is not a reading of anything — it is what
    every sync stores before the first Link — so the series begins with the
    first snapshot that contains an account. Without that, the owner's first
    real total would be drawn as a jump from a month of `$0.00`.
    """

    latest_per_day: dict[date, Snapshot] = {}
    for snapshot in snapshots:
        if snapshot.counts.account_count == 0:
            continue
        latest_per_day[snapshot.taken_at.astimezone(OWNER_ZONE).date()] = snapshot
    if not latest_per_day:
        return []
    first_day = _years_before(max(latest_per_day), HISTORY_YEARS)
    return [
        _history_point(snapshot)
        for day, snapshot in sorted(latest_per_day.items())
        if day >= first_day
    ]


def _history_point(snapshot: Snapshot) -> dict[str, object]:
    # Selected from `_total` rather than written out again, so a point and the
    # headline can never encode the same stored field two different ways.
    total = _total(snapshot)
    return {
        "taken_at": _timestamp(snapshot.taken_at),
        "total": {field: total[field] for field in _HISTORY_TOTAL_FIELDS},
    }


def _account(read: AccountRead) -> dict[str, object]:
    observation = read.observation
    freshness = read.freshness
    return {
        "account_id": read.account.id,
        "value_minor": None if observation is None else observation.figure.value_minor,
        "currency": read.account.currency,
        "sign": read.account.sign,
        "reconciliation_state": read.account.reconciliation_state.value,
        "item_state": None if read.item_state is None else read.item_state.value,
        "freshness": (
            None
            if freshness is None
            else {
                "state": freshness.state.value,
                "as_of": _optional_timestamp(freshness.source_as_of),
                "market_days_without_advance": freshness.market_days_without_advance,
                "is_carried_forward": freshness.is_carried_forward,
            }
        ),
    }


def _available_budget(budget: ItemBudget) -> dict[str, object]:
    return {
        "state": "AVAILABLE",
        "capacity": budget.capacity,
        "spent_count": budget.spent_count,
        "remaining": budget.remaining,
        "usable_count": len(budget.usable),
        "stranded_count": len(budget.stranded),
        "in_flight_count": len(budget.in_flight),
        "orphaned_count": len(budget.orphaned),
        "replacement_count": len(budget.replacements),
    }


def _budget(connection: sqlite3.Connection) -> dict[str, object]:
    try:
        return _available_budget(read_item_budget(connection))
    except ItemBudgetError:
        # The detailed exception may identify an Item. The phone needs the
        # refusal state and its class of cause, not host-side identifiers.
        return {"state": "UNAVAILABLE", "reason": "STORED_EVIDENCE_INCONSISTENT"}


def _alert(deliverable: DeliverableAlert) -> dict[str, object]:
    alert = deliverable.alert
    return {
        "kind": alert.kind.value,
        "subject": {
            "kind": "ITEM" if alert.kind.is_item_scoped else "ACCOUNT",
            "id": alert.subject_id,
        },
        "message": alert.message,
        "prompt": deliverable.prompt,
    }


def _plaintext(
    read: NetWorthRead,
    *,
    pairing_id: str,
    seq: str,
    published_at: str,
    publish_interval_seconds: int,
    grace_seconds: int,
    item_budget: dict[str, object],
    alerts: tuple[DeliverableAlert, ...],
    history: list[dict[str, object]],
) -> bytes:
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "pairing_id": pairing_id,
        "seq": seq,
        "published_at": published_at,
        "publish_interval_seconds": publish_interval_seconds,
        "grace_seconds": grace_seconds,
        "total": _total(read.snapshot),
        "connection_state": read.display_state.value,
        "accounts": [_account(account) for account in read.accounts],
        "item_budget": item_budget,
        "alerts": [_alert(alert) for alert in alerts],
        "history": history,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


class Publisher:
    """Publish one snapshot under the sole active pairing in one transaction."""

    __slots__ = (
        "_connection",
        "_grace_seconds",
        "_nonce_factory",
        "_publish_interval_seconds",
        "_resolve_key",
    )

    def __init__(
        self,
        connection: sqlite3.Connection,
        resolve_key: Callable[[str], bytes],
        *,
        nonce_factory: Callable[[int], bytes] = os.urandom,
        publish_interval_seconds: int = PUBLISH_INTERVAL_SECONDS,
        grace_seconds: int = GRACE_SECONDS,
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be a sqlite3.Connection")
        if not callable(resolve_key) or not callable(nonce_factory):
            raise TypeError("resolve_key and nonce_factory must be callable")
        for field, value in (
            ("publish_interval_seconds", publish_interval_seconds),
            ("grace_seconds", grace_seconds),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{field} must be an integer")
            if value <= 0:
                raise ValueError(f"{field} must be positive")
        self._connection = connection
        self._resolve_key = resolve_key
        self._nonce_factory = nonce_factory
        self._publish_interval_seconds = publish_interval_seconds
        self._grace_seconds = grace_seconds

    def publish(self, *, at: datetime) -> Publication:
        """Build, encrypt and replace the active envelope atomically.

        The caller must not have an open transaction: accepting one would make
        the promise to commit or roll back the publication locally depend on a
        later caller action. Reads, sequence allocation, old-envelope deletion,
        insertion, and prompt stamping all live under this one write lock.
        """

        require_utc(at, field="at")
        if self._connection.in_transaction:
            raise PublisherTransactionError("publisher requires a connection with no transaction")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            publication = self._publish_locked(at=at)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return publication

    def _publish_locked(self, *, at: datetime) -> Publication:
        pairings = self._connection.execute(
            "SELECT id, key_ref FROM pairing WHERE state = 'ACTIVE' ORDER BY id"
        ).fetchall()
        if len(pairings) != 1:
            raise PairingUnavailableError(
                f"publication requires exactly one active pairing; found {len(pairings)}"
            )
        pairing_id, key_ref = pairings[0]
        if not isinstance(pairing_id, str) or not pairing_id:
            raise PairingUnavailableError("active pairing id is not non-empty text")
        if not isinstance(key_ref, str) or not key_ref:
            raise PayloadKeyError("active pairing key_ref is not non-empty text")
        key = self._resolve_key(key_ref)
        if not isinstance(key, bytes) or len(key) != KEY_BYTES:
            raise PayloadKeyError("active pairing key must resolve to exactly 32 bytes")

        store = Store(self._connection)
        query = NetWorthQuery(store)
        read = query.latest()
        if read is None:
            raise PublisherError("no snapshot exists to publish")
        # Read under the same write lock as `latest()`, so the series ends on the
        # very snapshot this payload's headline carries.
        history = _history(query.history())
        alerts = AlertEvaluator(store.alerts).bulletin(at=at)
        budget = _budget(self._connection)

        row = self._connection.execute("SELECT coalesce(max(seq), 0) FROM publication").fetchone()
        if row is None or not isinstance(row[0], int):
            raise PublisherError("publication counter is not an integer")
        seq = int(row[0]) + 1
        seq_text = str(seq)
        published_at = _timestamp(at)
        plaintext = _plaintext(
            read,
            pairing_id=pairing_id,
            seq=seq_text,
            published_at=published_at,
            publish_interval_seconds=self._publish_interval_seconds,
            grace_seconds=self._grace_seconds,
            item_budget=budget,
            alerts=alerts,
            history=history,
        )
        nonce = self._nonce_factory(NONCE_BYTES)
        if not isinstance(nonce, bytes) or len(nonce) != NONCE_BYTES:
            raise PublisherError("nonce factory must return exactly 12 bytes")
        envelope = seal_payload(
            plaintext,
            key,
            schema_version=SCHEMA_VERSION,
            pairing_id=pairing_id,
            seq=seq_text,
            published_at=published_at,
            nonce=nonce,
        )

        cursor = self._connection.execute(
            """
            INSERT INTO publication(
                snapshot_id, pairing_id, seq, schema_version, published_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (read.snapshot.id, pairing_id, seq, SCHEMA_VERSION, published_at),
        )
        if cursor.lastrowid is None:
            raise PublisherError("SQLite did not assign a publication id")
        publication_id = int(cursor.lastrowid)

        # Section 6.3.1 requires this order. Inserting first would trip the
        # one-active-envelope index on every publication after the first.
        self._connection.execute("DELETE FROM published_envelope")
        self._connection.execute(
            """
            INSERT INTO published_envelope(
                publication_id, pairing_id, schema_version, seq, published_at,
                nonce, ciphertext, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                publication_id,
                pairing_id,
                SCHEMA_VERSION,
                seq_text,
                published_at,
                envelope.nonce,
                envelope.payload,
            ),
        )
        AlertEvaluator(store.alerts).record_prompted(alerts, at=at)
        return Publication(
            id=publication_id,
            snapshot_id=read.snapshot.id,
            seq=seq,
            envelope=envelope,
        )


__all__ = [
    "GRACE_SECONDS",
    "HISTORY_YEARS",
    "OWNER_ZONE",
    "PUBLISH_INTERVAL_SECONDS",
    "PairingUnavailableError",
    "PayloadKeyError",
    "Publication",
    "Publisher",
    "PublisherError",
    "PublisherTransactionError",
    "SCHEMA_VERSION",
]
