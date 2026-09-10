"""Read-only presentation seam for net-worth totals and account history.

Consumers receive immutable domain values whose amounts remain attached to
their source clocks.  This module deliberately exposes no mutation method: the
CLI, publisher, and eventual UI should not reach through it to a repository
that can append or update rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from networth.manual import PropertyRevisionLog
from networth.model import (
    DisplayState,
    FreshnessAssessment,
    FreshnessPolicy,
    ItemHealth,
    ItemState,
    Observation,
    ReconciliationState,
    Snapshot,
    SnapshotAccount,
)
from networth.staleness import StalenessMachine
from networth.store import Store


class NetWorthQueryError(RuntimeError):
    """Stored rows cannot form the presentation-safe read requested."""


@dataclass(frozen=True, slots=True)
class AccountRead:
    """One account as it contributed to the latest stored snapshot.

    A reconciled contributor carries both its immutable observation and the
    Axis-B assessment derived from that observation's source clock.  A ``NEW``
    account carries neither: it contributes nothing and is represented by its
    reconciliation and Item states instead of by an invented zero.
    """

    account: SnapshotAccount
    observation: Observation | None
    freshness: FreshnessAssessment | None
    item_state: ItemState | None

    def __post_init__(self) -> None:
        if not isinstance(self.account, SnapshotAccount):
            raise TypeError("account must be a SnapshotAccount")
        if self.observation is not None and not isinstance(self.observation, Observation):
            raise TypeError("observation must be an Observation or None")
        if self.freshness is not None and not isinstance(self.freshness, FreshnessAssessment):
            raise TypeError("freshness must be a FreshnessAssessment or None")
        if self.item_state is not None and not isinstance(self.item_state, ItemState):
            raise TypeError("item_state must be an ItemState or None")

        contributes = self.account.reconciliation_state is ReconciliationState.CONFIRMED
        if contributes != (self.observation is not None and self.freshness is not None):
            raise ValueError(
                "a confirmed account requires an observation and freshness; "
                "an unreconciled account carries neither"
            )
        if (self.account.item_id is not None) != (self.item_state is not None):
            raise ValueError("item_state is present exactly when the account names an Item")
        if self.freshness is not None:
            if self.observation is None:  # pragma: no cover - paired invariant above
                raise ValueError("freshness requires an observation")
            if self.freshness.source_as_of != self.observation.figure.as_of:
                raise ValueError("freshness must describe the observation's source clock")
            if self.freshness.item_state is not self.item_state:
                raise ValueError("freshness and account read must carry the same Item state")


@dataclass(frozen=True, slots=True)
class NetWorthRead:
    """The latest total and its presentation facts, all from one pure read."""

    snapshot: Snapshot
    accounts: tuple[AccountRead, ...]
    display_state: DisplayState

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, Snapshot):
            raise TypeError("snapshot must be a Snapshot")
        if not isinstance(self.accounts, tuple) or any(
            not isinstance(account, AccountRead) for account in self.accounts
        ):
            raise TypeError("accounts must be a tuple of AccountRead records")
        if not isinstance(self.display_state, DisplayState):
            raise TypeError("display_state must be a DisplayState")
        if len(self.accounts) != self.snapshot.counts.account_count:
            raise ValueError("account reads must match the snapshot's account_count")


class NetWorthQuery:
    """The only read surface intended for presentation and publication code."""

    __slots__ = ("_staleness", "_store")

    def __init__(self, store: Store, staleness: StalenessMachine | None = None) -> None:
        if not isinstance(store, Store):
            raise TypeError("store must be a Store")
        selected = StalenessMachine() if staleness is None else staleness
        if not isinstance(selected, StalenessMachine):
            raise TypeError("staleness must be a StalenessMachine")
        self._store = store
        self._staleness = selected

    def latest(self) -> NetWorthRead | None:
        """Read the latest snapshot with its per-account staleness facts.

        Advancing-clock accounts use observations from the snapshot's own
        successful run.  A manual-static account instead uses the revision that
        was both entered and in force when the snapshot was taken.  A later,
        back-dated edit therefore cannot redraw an already-stored total.
        """

        snapshot = self._store.snapshots.latest()
        if snapshot is None:
            return None

        accounts = self._store.accounts.for_snapshot()
        if len(accounts) != snapshot.counts.account_count:
            raise NetWorthQueryError(
                "the active account population no longer matches the latest snapshot; "
                "a new snapshot is required"
            )
        run_observations = {
            observation.account_id: observation
            for observation in self._store.observations.for_sync_run(snapshot.sync_run_id)
        }

        reads = tuple(
            self._account_read(
                account,
                snapshot=snapshot,
                run_observations=run_observations,
            )
            for account in accounts
        )
        display_state = self._staleness.display_state(
            (account.freshness for account in reads if account.freshness is not None),
            item_states=(
                account.item_state
                for account in reads
                if account.freshness is None and account.item_state is not None
            ),
            unreconciled_account_count=snapshot.counts.unreconciled_account_count,
        )
        return NetWorthRead(snapshot=snapshot, accounts=reads, display_state=display_state)

    def history(self) -> tuple[Snapshot, ...]:
        """Read the immutable total curve in chronological order."""

        return self._store.snapshots.history()

    def account_history(self, account_id: int) -> tuple[Observation, ...]:
        """Read one account's curve across every account id in its lineage."""

        if not isinstance(account_id, int) or isinstance(account_id, bool):
            raise TypeError("account_id must be an integer")
        if account_id <= 0:
            raise ValueError("account_id must be positive")
        return self._store.observations.history_for_lineage(account_id)

    def _account_read(
        self,
        account: SnapshotAccount,
        *,
        snapshot: Snapshot,
        run_observations: dict[int, Observation],
    ) -> AccountRead:
        item = self._item_for(account)
        item_state = None if item is None else item.status
        if account.reconciliation_state is ReconciliationState.NEW:
            return AccountRead(
                account=account,
                observation=None,
                freshness=None,
                item_state=item_state,
            )

        observation = self._observation_for(
            account,
            snapshot=snapshot,
            run_observations=run_observations,
        )
        freshness = self._staleness.assess(
            observation,
            policy=account.freshness_policy,
            item=item,
            at=snapshot.taken_at,
        )
        return AccountRead(
            account=account,
            observation=observation,
            freshness=freshness,
            item_state=item_state,
        )

    def _item_for(self, account: SnapshotAccount) -> ItemHealth | None:
        if account.item_id is None:
            return None
        item = self._store.items.get(account.item_id)
        if item is None:  # Foreign keys make this unreachable through a valid Store.
            raise NetWorthQueryError(f"account {account.id} names a missing Item")
        return item

    def _observation_for(
        self,
        account: SnapshotAccount,
        *,
        snapshot: Snapshot,
        run_observations: dict[int, Observation],
    ) -> Observation:
        if account.freshness_policy is FreshnessPolicy.MANUAL_STATIC:
            history = tuple(
                observation
                for observation in self.account_history(account.id)
                if observation.observed_at <= snapshot.taken_at
            )
            revision = PropertyRevisionLog.from_observations(history).as_of(snapshot.taken_at)
            if revision is not None:
                by_id = {observation.id: observation for observation in history}
                return by_id[revision.sequence]
        else:
            observation = run_observations.get(account.id)
            if observation is not None:
                return observation

        raise NetWorthQueryError(
            f"account {account.id} has no value represented by latest snapshot {snapshot.id}"
        )


__all__ = ["AccountRead", "NetWorthQuery", "NetWorthQueryError", "NetWorthRead"]
