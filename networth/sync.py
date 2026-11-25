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
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from networth.model import (
    FreshnessPolicy,
    HoldingsCheck,
    HoldingsCheckOutcome,
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
    check: HoldingsCheck | None = None


@dataclass(frozen=True, slots=True)
class SyncItemOutcome:
    """Retry accounting for one Item, separate from account-level failures."""

    item_id: int
    attempted: bool
    failed: bool


@dataclass(frozen=True, slots=True)
class FullSyncPlan:
    """Collected observations, not yet durable; contains no token material.

    Keep this in memory through a short write-transaction retry. A retry must
    replay ``persist`` with this same plan, never repeat the provider calls.
    Account figures remain sensitive; this object is not a log record.
    """

    attempted_count: int
    observations: tuple[ObservationDraft, ...]
    failures: tuple[SyncFailure, ...]
    items: tuple[SyncItemOutcome, ...]
    checks: tuple[HoldingsCheck, ...] = ()


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
        """Compatibility path; scheduled callers use collect, then persist.

        This method does not open an explicit transaction. The runtime must
        collect outside a transaction, then persist inside BEGIN IMMEDIATE.
        """

        return self.persist(self.collect(sync_run_id, at=at))

    def collect(
        self,
        sync_run_id: str,
        *,
        at: datetime | None = None,
        deferred_item_ids: frozenset[int] = frozenset(),
    ) -> FullSyncPlan:
        """Fetch and plan without writes; the caller must not hold a transaction."""

        self._store.items.require_no_transaction()
        require_nonempty(sync_run_id, field="sync_run_id")
        fetched_at = self._clock() if at is None else at
        require_utc(fetched_at, field="sync time")
        targets = self._store.accounts.syncable()
        by_item: dict[int, list[LinkedAccount]] = defaultdict(list)
        for target in targets:
            by_item[target.item_id].append(target)

        plans: list[_Plan] = []
        failures: list[SyncFailure] = []
        outcomes: list[SyncItemOutcome] = []
        for item_id, item_targets in by_item.items():
            if item_id in deferred_item_ids:
                item_plans, item_failures = self._fallback_all(
                    tuple(item_targets),
                    "RetryDeferred",
                    sync_run_id=sync_run_id,
                    fetched_at=fetched_at,
                )
                plans.extend(item_plans)
                failures.extend(item_failures)
                outcomes.append(SyncItemOutcome(item_id, attempted=False, failed=False))
                continue
            item_plans, item_failures = self._plan_item(
                item_id,
                tuple(item_targets),
                sync_run_id=sync_run_id,
                fetched_at=fetched_at,
            )
            plans.extend(item_plans)
            failures.extend(item_failures)
            outcomes.append(SyncItemOutcome(item_id, attempted=True, failed=bool(item_failures)))

        return FullSyncPlan(
            attempted_count=len(targets),
            observations=tuple(plan.draft for plan in plans),
            failures=tuple(failures),
            items=tuple(outcomes),
            checks=tuple(plan.check for plan in plans if plan.check is not None),
        )

    def persist(self, plan: FullSyncPlan) -> FullSyncResult:
        """Append a collected plan with no network I/O; never commit for the caller.

        A failed transaction can be rolled back and retried with this plan. A
        committed plan must not be replayed: the observation repository refuses
        a duplicate run/account key even when its payload is identical.
        """

        stored: list[Observation] = []
        for draft in plan.observations:
            observation = self._store.observations.append(draft)
            stored.append(observation)
            if not draft.is_carried_forward:
                self._store.accounts.record_fetch(
                    observation.account_id,
                    fetched_at=observation.fetched_at,
                    source_as_of=observation.figure.as_of,
                )
        for check in plan.checks:
            self._store.holdings_checks.append(check)

        return FullSyncResult(
            attempted_count=plan.attempted_count,
            observations=tuple(stored),
            failures=plan.failures,
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

            total = totals[0]
            value_minor, source_as_of, source_clock, check = self._holdings_value(
                target,
                total,
                account_holdings,
                item,
                sync_run_id=sync_run_id,
                fetched_at=fetched_at,
            )
            plans.append(
                _Plan(
                    draft=ObservationDraft(
                        sync_run_id=sync_run_id,
                        account_id=target.id,
                        observed_at=fetched_at,
                        figure=SourcedFigure(
                            value_minor=value_minor,
                            currency=total.currency,
                            as_of=source_as_of,
                            source_clock=source_clock,
                        ),
                        source=ObservationSource.PLAID_HOLDINGS,
                        fetched_at=fetched_at,
                        is_carried_forward=False,
                    ),
                    check=check,
                )
            )
        return plans, failures

    def _holdings_value(
        self,
        target: LinkedAccount,
        total: BalanceRecord,
        holdings: list[HoldingRecord],
        item: ItemHealth,
        *,
        sync_run_id: str,
        fetched_at: datetime,
    ) -> tuple[int, datetime | None, str, HoldingsCheck | None]:
        """An account's value, with a clock that dates that value and no other.

        Only the holdings carry clocks. The account total in the same reply "may
        be cached" (Plaid), and on 2026-11-23 one institution's stayed at the
        previous session's figure all day while its holdings were priced at the
        close — an old number that the holdings' date passed off as a fresh one.
        So the total keeps their clock only while it is their value. When it is
        not, the holdings are the value if they list the account's cash, since
        then nothing in the account is missing from them — and only while the
        total confirms them (``_check_holdings``); otherwise the total stands
        with no clock, because holdings without cash may be leaving some out,
        and holdings it does not confirm may be overstating it.
        """
        held = sum((holding.institution_value for holding in holdings), Decimal(0))
        check = None
        if _adds_up(total, held, len(holdings)):
            value = total.current
        elif any(holding.is_cash for holding in holdings):
            check = self._check_holdings(
                target, total, holdings, sync_run_id=sync_run_id, fetched_at=fetched_at
            )
            if check.outcome is HoldingsCheckOutcome.UNCONFIRMED:
                return check.total_minor, None, UNKNOWN, check
            value = held
        else:
            return to_minor_units(total.current, currency=total.currency), None, UNKNOWN, None
        source_as_of, source_clock = self._holdings_clock(holdings, item)
        return to_minor_units(value, currency=total.currency), source_as_of, source_clock, check

    def _check_holdings(
        self,
        target: LinkedAccount,
        total: BalanceRecord,
        holdings: list[HoldingRecord],
        *,
        sync_run_id: str,
        fetched_at: datetime,
    ) -> HoldingsCheck:
        """Whether the institution's own total confirms holdings that do not add up to it.

        Measured against Production on 2026-11-24: a total that stays at the
        previous close until its institution posts overnight equals that
        account's holdings revalued at Plaid's close prices for that session,
        within 0.00005% of it, while the holdings themselves are priced later in
        the day. So a total that adds up to the holdings at one session's close
        prices confirms their quantities and cash, and they are the value.

        The two sides do not advance together: Plaid moved its close prices
        hours after that session closed, while the total waited for the posting.
        A total that is the same figure as one already established still
        describes that session, so the verdict stands until a different total
        arrives. Any other total — margin debt the holdings omit, a transfer one
        side has not seen, a reader defect — is unconfirmed until a later pair
        is confirmed, and the account is valued by the total meanwhile.
        """
        total_minor = to_minor_units(total.current, currency=total.currency)
        at_close = self._at_close(holdings, fetched_at)
        close_session = None if at_close is None else at_close[0]
        if at_close is not None and _adds_up(total, at_close[1], len(holdings)):
            outcome, session = HoldingsCheckOutcome.CONFIRMED, close_session
        else:
            previous = self._store.holdings_checks.latest_for_account(target.id)
            session = (
                previous.session
                if previous is not None and previous.total_minor == total_minor
                else None
            )
            outcome = (
                HoldingsCheckOutcome.UNCONFIRMED
                if session is None
                else HoldingsCheckOutcome.UNCHANGED
            )
        return HoldingsCheck(
            sync_run_id=sync_run_id,
            account_id=target.id,
            checked_at=fetched_at,
            total_minor=total_minor,
            close_session=close_session,
            at_close_minor=(
                None if at_close is None else to_minor_units(at_close[1], currency=total.currency)
            ),
            outcome=outcome,
            session=session,
        )

    def _at_close(
        self,
        holdings: list[HoldingRecord],
        fetched_at: datetime,
    ) -> tuple[date, Decimal] | None:
        """The one market session whose close prices value these holdings, and that value.

        Cash is cash at any close. Every other holding needs Plaid's close price,
        all for one session, and it must be a session the calendar says had
        closed by the fetch: close prices from several sessions, or dated a day
        the market was shut, describe no figure an institution could publish.
        """
        session: date | None = None
        value = Decimal(0)
        for holding in holdings:
            if holding.is_cash:
                value += holding.institution_value
                continue
            if holding.value_at_close is None or holding.close_price_as_of is None:
                return None
            if session not in (None, holding.close_price_as_of):
                return None
            session = holding.close_price_as_of
            value += holding.value_at_close
        if session is None:
            return None
        close = self._calendar.session_close(session)
        if close is None or close > fetched_at:
            return None
        return session, value

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
            ),
            SyncFailure(
                account_id=target.id,
                error_type=error_type,
                carried_forward=True,
            ),
        )


def _adds_up(total: BalanceRecord, held: Decimal, count: int) -> bool:
    """Whether an account total is the value ``held`` of its ``count`` holdings.

    Each figure — the total and every holding's value — may be rounded by up to
    half a minor unit, so their sum may miss the total by that much per figure.
    Measured against Production on 2026-11-23: the accounts whose holdings are
    their total miss it by at most a fifth of that allowance, and the one whose
    total had not been refreshed missed it by percent, not cents.
    """
    per_unit = to_minor_units(Decimal(1), currency=total.currency)
    return abs(held - total.current) * per_unit * 2 <= count + 1


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
    "FullSyncPlan",
    "SyncFailure",
]
