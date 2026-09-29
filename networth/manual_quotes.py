"""Collect manual equity valuations before task 16 snapshots a named run.

Collection releases its read transaction before quote I/O. Persistence belongs
in the caller's short write transaction and rechecks the captured inputs so an
owner edit during I/O cannot attach an obsolete quantity to a new observation.
No scheduler, run success, carry-forward policy or publication is owned here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from networth.model import (
    EquityHolding,
    FreshnessPolicy,
    ObservationDraft,
    ObservationSource,
    Quote,
    SnapshotAccount,
    normalize_symbol,
    parse_share_count,
)
from networth.model.figure import require_nonempty, require_utc
from networth.store import Store


class ManualQuoteInputError(ValueError):
    """Stored manual inputs or a quote cannot support an honest valuation."""


class _Quotes(Protocol):
    def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]: ...


@dataclass(frozen=True, slots=True, repr=False)
class _Input:
    account: SnapshotAccount
    holding: EquityHolding


@dataclass(frozen=True, slots=True, repr=False)
class ManualQuotePlan:
    """Sensitive immutable values; keep in memory, never log or serialize."""

    inputs: tuple[_Input, ...]
    observations: tuple[ObservationDraft, ...]


class ManualQuoteWorker:
    def __init__(
        self,
        connection: sqlite3.Connection,
        quotes: _Quotes,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._db = connection
        self._store = Store(connection)
        self._quotes = quotes
        self._clock = clock

    def _inputs(self, *, at: datetime) -> tuple[_Input, ...]:
        result = []
        for account in self._store.accounts.for_snapshot():
            if account.freshness_policy is not FreshnessPolicy.MANUAL_QTY_LIVE_PRICE:
                continue
            row = self._db.execute(
                "SELECT kind, symbol, share_count, valued_as_of FROM manual_asset "
                "WHERE account_id = ?",
                (account.id,),
            ).fetchone()
            if row is None or row[0] != "EQUITY_SHARES":
                raise ManualQuoteInputError("manual equity account has no holding")
            try:
                confirmed = datetime.fromisoformat(row[3])
                holding = EquityHolding(
                    symbol=normalize_symbol(row[1]),
                    shares=parse_share_count(row[2]),
                    currency=account.currency,
                    set_on=confirmed,
                )
                if confirmed > at:
                    raise ValueError("future confirmation")
            except (TypeError, ValueError, AttributeError):
                raise ManualQuoteInputError("invalid stored manual holding") from None
            result.append(_Input(account, holding))
        return tuple(result)

    def collect(self, sync_run_id: str, *, at: datetime) -> ManualQuotePlan:
        require_nonempty(sync_run_id, field="sync_run_id")
        require_utc(at, field="at")
        if self._db.in_transaction:
            raise ValueError("quote collection requires no active transaction")
        # A single read snapshot covers account selection and every manual row.
        self._db.execute("BEGIN")
        try:
            inputs = self._inputs(at=at)
        finally:
            self._db.rollback()
        if not inputs:
            return ManualQuotePlan((), ())
        quotes = self._quotes.get_quotes(sorted({value.holding.symbol for value in inputs}))
        fetched = self._clock()
        require_utc(fetched, field="quote fetch time")
        if fetched < at:
            raise ManualQuoteInputError("quote completion precedes collection")
        observations = []
        for value in inputs:
            quote = quotes.get(value.holding.symbol)
            if quote is None:
                raise ManualQuoteInputError("a required manual equity quote is missing")
            try:
                figure = value.holding.value_with(quote)
                if quote.as_of > fetched:
                    raise ValueError("future quote")
            except (TypeError, ValueError):
                raise ManualQuoteInputError("invalid manual equity quote") from None
            observations.append(
                ObservationDraft(
                    sync_run_id=sync_run_id,
                    account_id=value.account.id,
                    observed_at=fetched,
                    fetched_at=fetched,
                    figure=figure,
                    source=ObservationSource.QUOTE,
                    is_carried_forward=False,
                )
            )
        return ManualQuotePlan(inputs, tuple(observations))

    def persist(self, plan: ManualQuotePlan, *, at: datetime) -> int:
        """Append once; caller owns BEGIN IMMEDIATE, commit, rollback and retry.

        A missing or invalid quote refuses the entire collection. It never
        becomes zero or a fresh copy of an old price. The caller must leave the
        cycle incomplete and retry collection; this seam never marks it successful.
        """
        require_utc(at, field="at")
        if not self._db.in_transaction:
            raise ValueError("quote persistence requires a caller-owned write transaction")
        if any(observation.observed_at > at for observation in plan.observations):
            raise ManualQuoteInputError("quote persistence precedes collection completion")
        if self._inputs(at=at) != plan.inputs:
            raise ManualQuoteInputError("manual inputs changed during quote collection")
        for observation in plan.observations:
            self._store.observations.append(observation)
            self._store.accounts.record_fetch(
                observation.account_id,
                fetched_at=observation.fetched_at,
                source_as_of=observation.figure.as_of,
            )
        return len(plan.observations)
