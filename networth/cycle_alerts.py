"""Assemble task 16's stored cycle facts before publication.

No provider calls or credentials belong here. Reads and evaluation share the
caller's write transaction so the health, account and manual facts cannot come
from different database versions. An old source stays old when reassessed now.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from networth.alerts import (
    AccountSignal,
    AlertEvaluation,
    AlertEvaluator,
    ShareCountObservation,
)
from networth.manual import NotARevisionError, PropertyRevisionLog
from networth.model import FreshnessPolicy, Observation, ReconciliationState, SnapshotAccount
from networth.model.figure import require_utc
from networth.staleness import StalenessMachine
from networth.store import Store


class CycleAlertInputError(ValueError):
    """Stored evidence cannot safely raise or resolve a cycle's alerts."""


class CycleAlertEvaluator:
    """Read positive facts for every active account and every stored Item.

    A missing price means no freshness assessment, not a healthy account. The
    same subject still supplies its reconciliation state and its explicitly read
    manual side. A missing manual row, unlike a missing price, is an observed
    absence and may resolve a share-count nudge. Bad stored clocks refuse the
    batch before the evaluator writes anything.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection
        self._store = Store(connection)
        self._staleness = StalenessMachine()

    def evaluate(self, *, at: datetime) -> AlertEvaluation:
        """Require a transaction; the dispatcher supplies BEGIN IMMEDIATE.

        The caller owns rollback/retry and commit before publish. This guard
        cannot distinguish a deferred transaction from a write reservation.
        """
        require_utc(at, field="alert evaluation time")
        if not self._db.in_transaction:
            raise ValueError("cycle alerts require a caller-owned write transaction")
        items = self._store.items.all()
        by_id = {item.id: item for item in items}
        if any(
            item.status_since > at or (item.last_polled_at is not None and item.last_polled_at > at)
            for item in items
        ):
            raise CycleAlertInputError("stored Item health clock is in the future")
        signals = []
        for account in self._store.accounts.for_alerts():
            item = None if account.item_id is None else by_id.get(account.item_id)
            if account.item_id is not None and item is None:  # pragma: no cover - foreign key
                raise CycleAlertInputError(f"account {account.id} names a missing Item")
            # Match Snapshotter and NetWorthQuery: reconciliation gates Axis B,
            # while Item health and the explicitly read manual side still count.
            observation = (
                None
                if account.reconciliation_state is ReconciliationState.NEW
                else self._observation(account, at=at)
            )
            freshness = None
            if observation is not None:
                if observation.observed_at > at or observation.fetched_at > at:
                    raise CycleAlertInputError("stored observation clock is in the future")
                freshness = self._staleness.assess(
                    observation, policy=account.freshness_policy, item=item, at=at
                )
            signals.append(
                AccountSignal(
                    account_id=account.id,
                    is_pending_reconciliation=(
                        account.reconciliation_state is ReconciliationState.NEW
                    ),
                    freshness=freshness,
                    share_count=self._share_count(account.id),
                )
            )
        return AlertEvaluator(self._store.alerts).evaluate(at=at, items=items, accounts=signals)

    def _observation(self, account: SnapshotAccount, *, at: datetime) -> Observation | None:
        if account.freshness_policy is not FreshnessPolicy.MANUAL_STATIC:
            return self._store.observations.latest_for_account(account.id)
        # The owner's effective valuation date chooses a property revision;
        # insertion order alone could pick a future revision instead.
        history = self._store.observations.history_for_lineage(account.id)
        try:
            revision = PropertyRevisionLog.from_observations(history).current(now=at)
        except NotARevisionError:
            raise CycleAlertInputError("invalid stored property revision lineage") from None
        if revision is None:
            return None
        return next(observation for observation in history if observation.id == revision.sequence)

    def _share_count(self, account_id: int) -> ShareCountObservation:
        row = self._db.execute(
            "SELECT kind, valued_as_of FROM manual_asset WHERE account_id = ?", (account_id,)
        ).fetchone()
        if row is None or row[0] == "REAL_PROPERTY":
            return ShareCountObservation(None)
        if row[0] != "EQUITY_SHARES":  # pragma: no cover - schema CHECK forbids it
            raise CycleAlertInputError("invalid stored manual asset kind")
        try:
            confirmed = datetime.fromisoformat(row[1])
            require_utc(confirmed, field="share-count confirmation")
        except (TypeError, ValueError):
            raise CycleAlertInputError("invalid stored share-count confirmation clock") from None
        return ShareCountObservation(confirmed)
