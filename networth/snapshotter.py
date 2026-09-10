"""Build the annotated total DESIGN section 10 requires, never a bare number."""

from __future__ import annotations

from datetime import datetime

from networth.manual import PropertyRevisionLog
from networth.model import (
    FreshnessPolicy,
    FreshnessState,
    ItemHealth,
    Observation,
    ReconciliationState,
    Snapshot,
    SnapshotAccount,
    SnapshotAge,
    SnapshotAgeState,
    SnapshotCounts,
    SnapshotDraft,
)
from networth.model.figure import require_nonempty, require_utc
from networth.staleness import StalenessMachine
from networth.store import Store


class SnapshotInputError(RuntimeError):
    """A successful run lacks the account facts needed for an honest total."""


class Snapshotter:
    """Compute and append one snapshot from a successful sync run.

    The only public result is :class:`~networth.model.snapshot.Snapshot`, whose
    aggregate figures cannot be separated from their tagged age and whose
    staleness counts are mandatory. There is deliberately no amount-only
    convenience method.

    Advancing-clock accounts must have an observation in the named run. A
    fixed manual valuation is different by design: it is a revision, so the
    latest revision in force at ``at`` contributes without being copied into
    every run. A missing current value therefore fails instead of silently
    making a successful run's total smaller.
    """

    __slots__ = ("_staleness", "_store")

    def __init__(self, store: Store, staleness: StalenessMachine | None = None) -> None:
        if not isinstance(store, Store):
            raise TypeError("store must be a Store")
        selected = StalenessMachine() if staleness is None else staleness
        if not isinstance(selected, StalenessMachine):
            raise TypeError("staleness must be a StalenessMachine")
        self._store = store
        self._staleness = selected

    def run(self, sync_run_id: str, *, at: datetime) -> Snapshot:
        """Compute at ``at`` and persist; the caller owns the transaction."""

        require_nonempty(sync_run_id, field="sync_run_id")
        require_utc(at, field="at")

        accounts = self._store.accounts.for_snapshot()
        run_observations = {
            observation.account_id: observation
            for observation in self._store.observations.for_sync_run(sync_run_id)
        }

        assets = 0
        liabilities = 0
        stale_count = 0
        unknown_count = 0
        static_count = 0
        reauth_count = 0
        unreconciled_count = 0
        carried_forward = False
        known_basis_clocks = []

        for account in accounts:
            item = self._item_for(account)
            if item is not None and item.status.owner_actionable:
                reauth_count += 1

            if account.reconciliation_state is ReconciliationState.NEW:
                unreconciled_count += 1
                continue

            observation = self._observation_for(
                account,
                run_observations=run_observations,
                sync_run_id=sync_run_id,
                at=at,
            )
            if observation.figure.currency != account.currency or account.currency != "USD":
                raise SnapshotInputError(
                    f"account {account.id} is not a single-currency USD contribution"
                )

            assessment = self._staleness.assess(
                observation,
                policy=account.freshness_policy,
                item=item,
                at=at,
            )
            if assessment.state in (FreshnessState.STALE, FreshnessState.FROZEN):
                stale_count += 1
            elif assessment.state is FreshnessState.UNKNOWN:
                unknown_count += 1
            elif assessment.state is FreshnessState.STATIC:
                static_count += 1

            if (
                assessment.state is not FreshnessState.STATIC
                and assessment.source_as_of is not None
            ):
                known_basis_clocks.append(assessment.source_as_of)

            carried_forward = carried_forward or observation.is_carried_forward
            if account.sign > 0:
                assets += observation.figure.value_minor
            else:
                liabilities += observation.figure.value_minor

        oldest_known = min(known_basis_clocks, default=None)
        if unknown_count:
            age = SnapshotAge(SnapshotAgeState.UNKNOWN, None, oldest_known)
        elif known_basis_clocks:
            age = SnapshotAge(SnapshotAgeState.KNOWN, oldest_known, oldest_known)
        else:
            age = SnapshotAge(SnapshotAgeState.STATIC_ONLY, None, None)

        counts = SnapshotCounts(
            account_count=len(accounts),
            stale_account_count=stale_count,
            unknown_freshness_account_count=unknown_count,
            static_account_count=static_count,
            reauth_account_count=reauth_count,
            unreconciled_account_count=unreconciled_count,
        )
        draft = SnapshotDraft(
            sync_run_id=sync_run_id,
            taken_at=at,
            net_worth=age.figure(assets - liabilities),
            assets=age.figure(assets),
            liabilities=age.figure(liabilities),
            counts=counts,
            is_complete=not carried_forward and stale_count == 0 and unreconciled_count == 0,
            age=age,
        )
        return self._store.snapshots.append(draft)

    def _item_for(self, account: SnapshotAccount) -> ItemHealth | None:
        if account.item_id is None:
            return None
        item = self._store.items.get(account.item_id)
        if item is None:  # Foreign keys make this unreachable through a valid Store.
            raise SnapshotInputError(f"account {account.id} names a missing Item")
        return item

    def _observation_for(
        self,
        account: SnapshotAccount,
        *,
        run_observations: dict[int, Observation],
        sync_run_id: str,
        at: datetime,
    ) -> Observation:
        if account.freshness_policy is FreshnessPolicy.MANUAL_STATIC:
            history = self._store.observations.history_for_lineage(account.id)
            revision = PropertyRevisionLog.from_observations(history).current(now=at)
            if revision is None:
                raise SnapshotInputError(
                    f"manual-static account {account.id} has no valuation in force"
                )
            by_id = {observation.id: observation for observation in history}
            return by_id[revision.sequence]

        observation = run_observations.get(account.id)
        if observation is None:
            raise SnapshotInputError(
                f"account {account.id} has no observation in successful run {sync_run_id!r}"
            )
        return observation


__all__ = ["SnapshotInputError", "Snapshotter"]
