"""Turn a newly linked Item's accounts into rows the rest of the daemon can see.

Link finalization (``07a``) ends by committing an ``item`` row, and until this
module nothing went further: no code anywhere wrote an ``account`` row. Every
later stage reads that table — ``FullSync`` iterates ``accounts.syncable()``,
``Snapshotter`` sums ``accounts.for_snapshot()`` — and every one of their tests
inserts its own rows in a fixture, so each stage was complete, tested, and fed
by nothing. A real Link would have spent a lifetime Item slot (**F2**) and
produced an Item with no accounts, no observation, no snapshot and no
publication.

This is the missing step, and it is deliberately one step: ask the provider
what accounts sit behind an Item, **once**, and record the ones v0 models.

**What v0 models, and what it leaves out on purpose** (``DESIGN.md`` §1, §3):

- ``investment`` and ``depository`` accounts in USD are assets, ``sign = +1``.
  Investments are dated by their holdings; cash is dated by its balance.
- ``credit`` and ``loan`` accounts are **not created**. v0 is assets only —
  cards were deferred by the owner on 2026-08-30 — and §10 states that "v0 has
  no liability accounts at all". A liability row excluded from the total would
  be a category the snapshot says cannot exist.
- Anything not denominated in USD is **not created** either. Multi-currency is
  a stated non-goal, and ``Snapshotter`` refuses an included non-USD account
  outright, so one such row would cost every other account its snapshot.

Both omissions are counted in :class:`DiscoveryResult` rather than passed over,
because an account left out of a total is the one thing this project may never
do quietly. The counts carry no name, identifier or figure.

**Whether a new account contributes immediately** is §8.5's question. That
section holds a *replacement* Item's accounts at ``NEW`` until the owner maps
them onto the archived ones, because counting both is "the owner suddenly got
richer". The hazard needs an archived predecessor to exist. So an account is
``CONFIRMED`` on arrival exactly when nothing could be double-counted — the
Item replaces no other and the database holds no archived account — and ``NEW``
otherwise, which leaves §8.5's flow untouched for the case it was written for.

**Once per Item, recorded on the Item.** ``accounts_discovered_at`` is what
makes "asked, and found nothing v0 models" different from "never asked".
Accounts opened at the institution later are not picked up here; that is update
mode's job (task ``09``), not a second purpose for this module.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from networth.filelock import exclusive_file_lock
from networth.model.figure import require_utc
from networth.plaid.client import AccountDescriptor

SUPPORTED_CURRENCY = "USD"

#: Plaid account type → the freshness policy that dates it (§8.1). The keys are
#: the whole of what v0 creates; every other type is counted and left out.
ASSET_POLICIES = {
    "investment": "SYNCED_HOLDINGS",
    "depository": "SYNCED_BALANCE",
}

#: Items worth asking. ``NEEDS_REAUTH`` and ``REVOKED`` cannot answer, and the
#: health poller owns moving an Item out of them.
_ASKABLE_STATES = ("HEALTHY", "DEGRADED")


class _DescriptorClient(Protocol):
    def list_accounts(self, access_token: str) -> tuple[AccountDescriptor, ...]: ...


class _Secret(Protocol):
    def reveal(self) -> str: ...


class _TokenResolver(Protocol):
    def get(self, secret_ref: str) -> _Secret: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _text(at: datetime) -> str:
    require_utc(at, field="discovery time")
    return at.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Counts only — no account, Item or institution identity, and no figure.

    ``accounts_pending`` is the subset of ``accounts_created`` held at ``NEW``.
    ``accounts_left_out`` is what the provider listed and v0 does not model.
    """

    items_due: int = 0
    items_discovered: int = 0
    items_failed: int = 0
    accounts_created: int = 0
    accounts_pending: int = 0
    accounts_left_out: int = 0

    @property
    def ok(self) -> bool:
        """False when an Item could not be asked and will be asked again."""
        return self.items_failed == 0


class AccountDiscovery:
    """Create the account rows of every linked Item that has not been asked yet.

    Follows the dispatchers' contract in :mod:`networth.dispatch`: the caller
    supplies the canonical sync lock for the selected database, the connection
    must be idle, every provider call finishes before the first write, and each
    Item's rows land in one short ``BEGIN IMMEDIATE`` transaction with its
    marker. A failed Item is left unmarked and is asked again on the next
    activation; it never blocks the Items after it.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        client: _DescriptorClient,
        tokens: _TokenResolver,
        *,
        lock_path: Path,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._db = connection
        self._idle()
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._client = client
        self._tokens = tokens
        self._lock_path = lock_path
        self._clock = clock

    def _idle(self) -> None:
        if self._db.in_transaction:
            raise ValueError("discovery requires a connection with no active transaction")

    def run_due(self) -> DiscoveryResult:
        self._idle()
        with exclusive_file_lock(self._lock_path, blocking=False, reentrant=False):
            return self._run_locked()

    def _run_locked(self) -> DiscoveryResult:
        placeholders = ", ".join("?" for _ in _ASKABLE_STATES)
        due = self._db.execute(
            "SELECT id, secret_ref, replaces_item_id FROM item "
            f"WHERE accounts_discovered_at IS NULL AND status IN ({placeholders}) "
            "ORDER BY id",
            _ASKABLE_STATES,
        ).fetchall()

        discovered = failed = created = pending = left_out = 0
        for item_id, secret_ref, replaces_item_id in due:
            self._idle()
            try:
                descriptors = self._client.list_accounts(self._tokens.get(secret_ref).reveal())
            except Exception:
                # Deliberately broad, and deliberately silent about the cause:
                # a provider or token-store exception can carry response text.
                # The Item stays unmarked, which is the whole of the retry.
                failed += 1
                continue
            item_created, item_pending, item_left_out = self._record(
                item_id, descriptors, is_replacement=replaces_item_id is not None
            )
            discovered += 1
            created += item_created
            pending += item_pending
            left_out += item_left_out

        return DiscoveryResult(
            items_due=len(due),
            items_discovered=discovered,
            items_failed=failed,
            accounts_created=created,
            accounts_pending=pending,
            accounts_left_out=left_out,
        )

    def _record(
        self,
        item_id: int,
        descriptors: tuple[AccountDescriptor, ...],
        *,
        is_replacement: bool,
    ) -> tuple[int, int, int]:
        at = _text(self._clock())
        created = pending = left_out = 0
        try:
            self._db.execute("BEGIN IMMEDIATE")
            # §8.5: only an archived predecessor can be double-counted. Read
            # inside the write lock, so a concurrent archive cannot slip between
            # this answer and the rows it decides.
            has_predecessor = is_replacement or (
                self._db.execute(
                    "SELECT 1 FROM account WHERE reconciliation_state = 'ARCHIVED' "
                    "OR archived_at IS NOT NULL LIMIT 1"
                ).fetchone()
                is not None
            )
            state = "NEW" if has_predecessor else "CONFIRMED"
            for descriptor in descriptors:
                policy = ASSET_POLICIES.get(descriptor.type)
                if policy is None or descriptor.currency != SUPPORTED_CURRENCY:
                    left_out += 1
                    continue
                cursor = self._db.execute(
                    "INSERT INTO account(item_id, plaid_account_id, name, official_name, "
                    "mask, type, subtype, currency, sign, freshness_policy, "
                    "include_in_net_worth, reconciliation_state, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 1, ?, ?) "
                    "ON CONFLICT(item_id, plaid_account_id) "
                    "WHERE plaid_account_id IS NOT NULL DO NOTHING",
                    (
                        item_id,
                        descriptor.account_id,
                        descriptor.name,
                        descriptor.official_name,
                        descriptor.mask,
                        descriptor.type,
                        descriptor.subtype,
                        descriptor.currency,
                        policy,
                        state,
                        at,
                    ),
                )
                if cursor.rowcount == 1:
                    created += 1
                    pending += state == "NEW"
            self._db.execute(
                "UPDATE item SET accounts_discovered_at = ? "
                "WHERE id = ? AND accounts_discovered_at IS NULL",
                (at, item_id),
            )
            self._db.commit()
        except BaseException:
            if self._db.in_transaction:
                self._db.rollback()
            raise
        return created, pending, left_out


__all__ = [
    "ASSET_POLICIES",
    "SUPPORTED_CURRENCY",
    "AccountDiscovery",
    "DiscoveryResult",
]
