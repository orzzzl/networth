"""Full Plaid sync: provider records become append-only account observations.

The network phase finishes before the first SQLite write, so a slow second Item
cannot hold the database transaction opened by the first. Provider failures are
account-local: the last observation is carried forward when one exists, with
both of its clocks unchanged and ``is_carried_forward`` set explicitly.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Protocol

from networth.model import (
    FreshnessPolicy,
    ItemHealth,
    LinkedAccount,
    Observation,
    ObservationDraft,
    ObservationSource,
    SourcedFigure,
    to_minor_units,
)
from networth.model.figure import require_nonempty, require_utc
from networth.plaid import BalanceRecord, HoldingRecord, InvestmentRecords
from networth.staleness import UsEquityMarketCalendar
from networth.store import Store

UNKNOWN = "UNKNOWN"
BALANCE_LAST_UPDATED = "BALANCE_LAST_UPDATED_DATETIME"
BALANCE_REALTIME_FETCH = "BALANCE_REALTIME_FETCHED_AT"
HOLDING_PRICE_DATETIME = "INSTITUTION_PRICE_DATETIME"
HOLDING_PRICE_AS_OF = "INSTITUTION_PRICE_AS_OF"
INVESTMENTS_STATUS_UPDATE = "INVESTMENTS_LAST_SUCCESSFUL_UPDATE"


class BalanceMode(StrEnum):
    """The explicit F5 cost/freshness choice."""

    REALTIME = "realtime"
    CACHED = "cached"


class _SyncClient(Protocol):
    def fetch_realtime_balances(self, access_token: str) -> tuple[BalanceRecord, ...]: ...

    def fetch_cached_balances(self, access_token: str) -> tuple[BalanceRecord, ...]: ...

    def fetch_holdings(self, access_token: str) -> InvestmentRecords: ...


class _Secret(Protocol):
    def reveal(self) -> str: ...


class _TokenResolver(Protocol):
    def get(self, secret_ref: str) -> _Secret: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SyncFailure:
    """A target that did not produce a fresh observation, without response data."""

    account_id: int
    error_type: str
    carried_forward: bool


@dataclass(frozen=True, slots=True)
class FullSyncResult:
    """The durable rows and every target that had to reuse or omit a value."""

    attempted_count: int
    observations: tuple[Observation, ...]
    failures: tuple[SyncFailure, ...]

    @property
    def carried_forward_count(self) -> int:
        return sum(failure.carried_forward for failure in self.failures)

    @property
    def missing_count(self) -> int:
        return sum(not failure.carried_forward for failure in self.failures)

    @property
    def ok(self) -> bool:
        return not self.failures


@dataclass(frozen=True, slots=True)
class _Plan:
    draft: ObservationDraft
    fresh: bool


class FullSync:
    """Fetch every active linked account and append one observation per result."""

    def __init__(
        self,
        store: Store,
        client: _SyncClient,
        tokens: _TokenResolver,
        *,
        balance_mode: BalanceMode,
        calendar: UsEquityMarketCalendar | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not isinstance(store, Store):
            raise TypeError("store must be a Store")
        if not isinstance(balance_mode, BalanceMode):
            raise TypeError("balance_mode must be a BalanceMode")
        selected_calendar = UsEquityMarketCalendar() if calendar is None else calendar
        if not isinstance(selected_calendar, UsEquityMarketCalendar):
            raise TypeError("calendar must be a UsEquityMarketCalendar")
        self._store = store
        self._client = client
        self._tokens = tokens
        self._balance_mode = balance_mode
        self._calendar = selected_calendar
        self._clock = clock

    def run(self, sync_run_id: str, *, at: datetime | None = None) -> FullSyncResult:
        """Fetch first, then append; the caller owns the enclosing transaction."""

        require_nonempty(sync_run_id, field="sync_run_id")
        fetched_at = self._clock() if at is None else at
        require_utc(fetched_at, field="sync time")
        targets = self._store.accounts.syncable()
        by_item: dict[int, list[LinkedAccount]] = defaultdict(list)
        for target in targets:
            by_item[target.item_id].append(target)

        plans: list[_Plan] = []
        failures: list[SyncFailure] = []
        for item_id, item_targets in by_item.items():
            item_plans, item_failures = self._plan_item(
                item_id,
                tuple(item_targets),
                sync_run_id=sync_run_id,
                fetched_at=fetched_at,
            )
            plans.extend(item_plans)
            failures.extend(item_failures)

        # No network call occurs below this line. SQLite may open its write
        # transaction on the first append without holding it across the wire.
        stored: list[Observation] = []
        for plan in plans:
            observation = self._store.observations.append(plan.draft)
            stored.append(observation)
            if plan.fresh:
                self._store.accounts.record_fetch(
                    observation.account_id,
                    fetched_at=observation.fetched_at,
                    source_as_of=observation.figure.as_of,
                )

        return FullSyncResult(
            attempted_count=len(targets),
            observations=tuple(stored),
            failures=tuple(failures),
        )

    def _plan_item(
        self,
        item_id: int,
        targets: tuple[LinkedAccount, ...],
        *,
        sync_run_id: str,
        fetched_at: datetime,
    ) -> tuple[list[_Plan], list[SyncFailure]]:
        item = self._store.items.get(item_id)
        if item is None:
            return self._fallback_all(
                targets,
                "ItemNotFoundError",
                sync_run_id=sync_run_id,
                fetched_at=fetched_at,
            )

        try:
            access_token = self._tokens.get(item.secret_ref).reveal()
        except Exception as exc:
            return self._fallback_all(
                targets,
                type(exc).__name__,
                sync_run_id=sync_run_id,
                fetched_at=fetched_at,
            )

        balance_targets = tuple(
            target
            for target in targets
            if target.freshness_policy is FreshnessPolicy.SYNCED_BALANCE
        )
        holding_targets = tuple(
            target
            for target in targets
            if target.freshness_policy is FreshnessPolicy.SYNCED_HOLDINGS
        )
        plans: list[_Plan] = []
        failures: list[SyncFailure] = []

        if balance_targets:
            try:
                balances = (
                    self._client.fetch_realtime_balances(access_token)
                    if self._balance_mode is BalanceMode.REALTIME
                    else self._client.fetch_cached_balances(access_token)
                )
            except Exception as exc:
                failed_plans, failed = self._fallback_all(
                    balance_targets,
                    type(exc).__name__,
                    sync_run_id=sync_run_id,
                    fetched_at=fetched_at,
                )
                plans.extend(failed_plans)
                failures.extend(failed)
            else:
                product_plans, product_failures = self._plan_balances(
                    balance_targets,
                    balances,
                    sync_run_id=sync_run_id,
                    fetched_at=fetched_at,
                )
                plans.extend(product_plans)
                failures.extend(product_failures)

        if holding_targets:
            try:
                investments = self._client.fetch_holdings(access_token)
            except Exception as exc:
                failed_plans, failed = self._fallback_all(
                    holding_targets,
                    type(exc).__name__,
                    sync_run_id=sync_run_id,
                    fetched_at=fetched_at,
                )
                plans.extend(failed_plans)
                failures.extend(failed)
            else:
                product_plans, product_failures = self._plan_holdings(
                    holding_targets,
                    investments,
                    item,
                    sync_run_id=sync_run_id,
                    fetched_at=fetched_at,
                )
                plans.extend(product_plans)
                failures.extend(product_failures)

        return plans, failures

    def _plan_balances(
        self,
        targets: tuple[LinkedAccount, ...],
        records: tuple[BalanceRecord, ...],
        *,
        sync_run_id: str,
        fetched_at: datetime,
    ) -> tuple[list[_Plan], list[SyncFailure]]:
        by_account: dict[str, list[BalanceRecord]] = defaultdict(list)
        for record in records:
            by_account[record.account_id].append(record)

        plans: list[_Plan] = []
        failures: list[SyncFailure] = []
        for target in targets:
            matches = by_account.get(target.plaid_account_id, [])
            error = None
            if not matches:
                error = "ProviderAccountMissingError"
            elif len(matches) != 1:
                error = "DuplicateProviderAccountError"
            elif matches[0].currency != target.currency:
                error = "CurrencyMismatchError"

            if error is not None:
                plan, failure = self._fallback(
                    target,
                    error,
                    sync_run_id=sync_run_id,
                    fetched_at=fetched_at,
                )
                if plan is not None:
                    plans.append(plan)
                failures.append(failure)
                continue

            record = matches[0]
            if self._balance_mode is BalanceMode.REALTIME:
                source_as_of = record.last_updated_datetime or fetched_at
                source_clock = (
                    BALANCE_LAST_UPDATED
                    if record.last_updated_datetime is not None
                    else BALANCE_REALTIME_FETCH
                )
            else:
                source_as_of = None
                source_clock = UNKNOWN
            plans.append(
                _Plan(
                    draft=ObservationDraft(
                        sync_run_id=sync_run_id,
                        account_id=target.id,
                        observed_at=fetched_at,
                        figure=SourcedFigure(
                            value_minor=to_minor_units(record.current, currency=record.currency),
                            currency=record.currency,
                            as_of=source_as_of,
                            source_clock=source_clock,
                        ),
                        source=ObservationSource.PLAID_BALANCE,
                        fetched_at=fetched_at,
                        is_carried_forward=False,
                    ),
                    fresh=True,
                )
            )
        return plans, failures

    def _plan_holdings(
        self,
        targets: tuple[LinkedAccount, ...],
        investments: InvestmentRecords,
        item: ItemHealth,
        *,
        sync_run_id: str,
        fetched_at: datetime,
    ) -> tuple[list[_Plan], list[SyncFailure]]:
        accounts: dict[str, list[BalanceRecord]] = defaultdict(list)
        holdings: dict[str, list[HoldingRecord]] = defaultdict(list)
        for account_record in investments.accounts:
            accounts[account_record.account_id].append(account_record)
        for holding_record in investments.holdings:
            holdings[holding_record.account_id].append(holding_record)

        plans: list[_Plan] = []
        failures: list[SyncFailure] = []
        for target in targets:
            totals = accounts.get(target.plaid_account_id, [])
            account_holdings = holdings.get(target.plaid_account_id, [])
            currencies = {holding.currency for holding in account_holdings}
            error = None
            if not totals:
                error = "ProviderAccountMissingError"
            elif len(totals) != 1:
                error = "DuplicateProviderAccountError"
            elif totals[0].currency != target.currency or currencies - {target.currency}:
                error = "CurrencyMismatchError"

            if error is not None:
                plan, failure = self._fallback(
                    target,
                    error,
                    sync_run_id=sync_run_id,
                    fetched_at=fetched_at,
                )
                if plan is not None:
                    plans.append(plan)
                failures.append(failure)
                continue

            source_as_of, source_clock = self._holdings_clock(account_holdings, item)
            total = totals[0]
            plans.append(
                _Plan(
                    draft=ObservationDraft(
                        sync_run_id=sync_run_id,
                        account_id=target.id,
                        observed_at=fetched_at,
                        figure=SourcedFigure(
                            value_minor=to_minor_units(total.current, currency=total.currency),
                            currency=total.currency,
                            as_of=source_as_of,
                            source_clock=source_clock,
                        ),
                        source=ObservationSource.PLAID_HOLDINGS,
                        fetched_at=fetched_at,
                        is_carried_forward=False,
                    ),
                    fresh=True,
                )
            )
        return plans, failures

    def _holdings_clock(
        self,
        holdings: list[HoldingRecord],
        item: ItemHealth,
    ) -> tuple[datetime | None, str]:
        if not holdings or item.investments_last_successful_update is None:
            return None, UNKNOWN

        evidence: list[tuple[datetime, str]] = []
        for holding in holdings:
            clock = self._holding_price_clock(holding)
            if clock is None:
                return None, UNKNOWN
            evidence.append(clock)
        evidence.append((item.investments_last_successful_update, INVESTMENTS_STATUS_UPDATE))

        oldest = min(instant for instant, _ in evidence)
        sources = sorted({source for instant, source in evidence if instant == oldest})
        return oldest, "+".join(sources)

    def _holding_price_clock(self, holding: HoldingRecord) -> tuple[datetime, str] | None:
        precise = holding.institution_price_datetime
        if precise is not None:
            if precise.time() != time(0):
                return precise, HOLDING_PRICE_DATETIME
            date_clock = self._market_close(precise.date())
            if date_clock is not None:
                return date_clock, HOLDING_PRICE_DATETIME

        if holding.institution_price_as_of is None:
            return None
        date_clock = self._market_close(holding.institution_price_as_of)
        if date_clock is None:
            return None
        return date_clock, HOLDING_PRICE_AS_OF

    def _market_close(self, day: date) -> datetime | None:
        return self._calendar.session_close(day)

    def _fallback_all(
        self,
        targets: tuple[LinkedAccount, ...],
        error_type: str,
        *,
        sync_run_id: str,
        fetched_at: datetime,
    ) -> tuple[list[_Plan], list[SyncFailure]]:
        plans: list[_Plan] = []
        failures: list[SyncFailure] = []
        for target in targets:
            plan, failure = self._fallback(
                target,
                error_type,
                sync_run_id=sync_run_id,
                fetched_at=fetched_at,
            )
            if plan is not None:
                plans.append(plan)
            failures.append(failure)
        return plans, failures

    def _fallback(
        self,
        target: LinkedAccount,
        error_type: str,
        *,
        sync_run_id: str,
        fetched_at: datetime,
    ) -> tuple[_Plan | None, SyncFailure]:
        previous = self._store.observations.latest_for_account(target.id)
        if previous is None:
            return None, SyncFailure(
                account_id=target.id,
                error_type=error_type,
                carried_forward=False,
            )
        return (
            _Plan(
                draft=ObservationDraft(
                    sync_run_id=sync_run_id,
                    account_id=target.id,
                    observed_at=fetched_at,
                    figure=previous.figure,
                    source=previous.source,
                    fetched_at=previous.fetched_at,
                    is_carried_forward=True,
                ),
                fresh=False,
            ),
            SyncFailure(
                account_id=target.id,
                error_type=error_type,
                carried_forward=True,
            ),
        )


__all__ = [
    "BALANCE_LAST_UPDATED",
    "BALANCE_REALTIME_FETCH",
    "HOLDING_PRICE_AS_OF",
    "HOLDING_PRICE_DATETIME",
    "INVESTMENTS_STATUS_UPDATE",
    "UNKNOWN",
    "BalanceMode",
    "FullSync",
    "FullSyncResult",
    "SyncFailure",
]
