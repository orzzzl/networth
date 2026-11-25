"""Task 12: holdings and balances become honest append-only observations."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import plaid
import pytest
from plaid.model.investments_holdings_get_response import InvestmentsHoldingsGetResponse

from networth.model import (
    FreshnessPolicy,
    ObservationDraft,
    ObservationSource,
    SourcedFigure,
)
from networth.plaid import BalanceRecord, HoldingRecord, InvestmentRecords
from networth.plaid.client import PlaidClient
from networth.storage import migrate
from networth.store import ObservationConflictError, Store
from networth.sync import (
    BALANCE_LAST_UPDATED,
    BALANCE_REALTIME_FETCH,
    HOLDING_PRICE_AS_OF,
    HOLDING_PRICE_DATETIME,
    INVESTMENTS_STATUS_UPDATE,
    UNKNOWN,
    BalanceMode,
    FullSync,
)
from tests.fake_plaid import FakeSandboxApi
from tests.test_plaid_client import CREDENTIALS

NOW = datetime(2026, 1, 15, 18, 0, tzinfo=UTC)
SOURCE = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
OLDER_FETCH = datetime(2026, 1, 14, 18, 0, tzinfo=UTC)
OLDER_SOURCE = datetime(2026, 1, 13, 21, 0, tzinfo=UTC)


def _db_time(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    connection.execute(
        "INSERT INTO institution(plaid_institution_id, name, is_oauth) "
        "VALUES ('synthetic-institution', 'Synthetic institution', 0)"
    )
    try:
        yield connection
    finally:
        connection.close()


def add_item(
    connection: sqlite3.Connection,
    suffix: str,
    *,
    investments_update: datetime | None = SOURCE,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO item(
            institution_id, plaid_item_id, secret_ref, status, status_since,
            created_at, investments_last_successful_update
        ) VALUES (1, ?, ?, 'HEALTHY', ?, ?, ?)
        """,
        (
            f"plaid-item-{suffix}",
            f"secret-ref-{suffix}",
            _db_time(OLDER_FETCH),
            _db_time(OLDER_FETCH),
            None if investments_update is None else _db_time(investments_update),
        ),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def add_account(
    connection: sqlite3.Connection,
    item_id: int | None,
    suffix: str,
    *,
    policy: FreshnessPolicy = FreshnessPolicy.SYNCED_BALANCE,
    reconciliation: str = "CONFIRMED",
    archived_at: datetime | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO account(
            item_id, plaid_account_id, name, type, currency, sign,
            freshness_policy, include_in_net_worth, reconciliation_state,
            created_at, archived_at
        ) VALUES (?, ?, ?, 'synthetic', 'USD', 1, ?, 1, ?, ?, ?)
        """,
        (
            item_id,
            None if item_id is None else f"account-{suffix}",
            f"Synthetic {suffix}",
            policy.value,
            reconciliation,
            _db_time(OLDER_FETCH),
            None if archived_at is None else _db_time(archived_at),
        ),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def add_run(connection: sqlite3.Connection, run_id: str, at: datetime = NOW) -> None:
    connection.execute(
        """
        INSERT INTO sync_run(id, started_at, "trigger") VALUES (?, ?, 'TEST')
        """,
        (run_id, _db_time(at)),
    )


class FakeSecret:
    def __init__(self, material: str) -> None:
        self._material = material

    def reveal(self) -> str:
        return self._material

    def __repr__(self) -> str:
        return "<FakeSecret: redacted>"


class FakeTokens:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def get(self, secret_ref: str) -> FakeSecret:
        self.requests.append(secret_ref)
        return FakeSecret(secret_ref.replace("secret-ref", "material"))


class FakeClient:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        realtime: tuple[BalanceRecord, ...] | Exception = (),
        cached: tuple[BalanceRecord, ...] | Exception = (),
        investments: InvestmentRecords | Exception | None = None,
    ) -> None:
        self._connection = connection
        self._realtime = realtime
        self._cached = cached
        self._investments = InvestmentRecords((), ()) if investments is None else investments
        self.calls: list[tuple[str, str]] = []

    def _answer(self, name: str, token: str, answer: object) -> object:
        assert not self._connection.in_transaction, "network calls must precede every write"
        self.calls.append((name, token))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def fetch_realtime_balances(self, access_token: str) -> tuple[BalanceRecord, ...]:
        return self._answer("realtime", access_token, self._realtime)  # type: ignore[return-value]

    def fetch_cached_balances(self, access_token: str) -> tuple[BalanceRecord, ...]:
        return self._answer("cached", access_token, self._cached)  # type: ignore[return-value]

    def fetch_holdings(self, access_token: str) -> InvestmentRecords:
        return self._answer("holdings", access_token, self._investments)  # type: ignore[return-value]


def balance(
    suffix: str,
    amount: str = "123.45",
    *,
    updated: datetime | None = SOURCE,
) -> BalanceRecord:
    return BalanceRecord(
        account_id=f"account-{suffix}",
        current=Decimal(amount),
        currency="USD",
        last_updated_datetime=updated,
    )


def holding(
    suffix: str,
    amount: str,
    *,
    price_datetime: datetime | None = None,
    price_as_of: date | None = None,
) -> HoldingRecord:
    return HoldingRecord(
        account_id=f"account-{suffix}",
        institution_value=Decimal(amount),
        currency="USD",
        institution_price_datetime=price_datetime,
        institution_price_as_of=price_as_of,
        is_cash=False,
    )


@dataclass(frozen=True)
class Position:
    """One holding of a synthetic reply, and what Plaid says its security closed at.

    The holding is valued at ``quantity`` × ``price``, the institution's price;
    ``close`` is the security's ``close_price`` for the session ``close_as_of``,
    which a total a session behind its holdings is made of.
    """

    kind: str
    quantity: str
    price: str
    close: str | None = None
    close_as_of: date | None = None


def cash(amount: str) -> Position:
    return Position("cash", amount, "1", "1", None)


def _sdk_security(index: int, position: Position) -> dict[str, Any]:
    return {
        "security_id": f"security-{index}",
        "isin": None,
        "cusip": None,
        "sedol": None,
        "institution_security_id": None,
        "institution_id": None,
        "proxy_security_id": None,
        "name": f"Synthetic {position.kind} {index}",
        "ticker_symbol": "CUR:USD" if position.kind == "cash" else f"SYN{index}",
        "is_cash_equivalent": position.kind == "cash",
        "type": position.kind,
        "close_price": None if position.close is None else float(position.close),
        "close_price_as_of": (
            None if position.close_as_of is None else position.close_as_of.isoformat()
        ),
        "iso_currency_code": "USD",
        "unofficial_currency_code": None,
        "market_identifier_code": None,
        "sector": None,
        "industry": None,
        "cfi_code": None,
        "figi": None,
        "option_contract": None,
        "fixed_income": None,
    }


def sdk_holdings_client(
    total: str,
    holdings: tuple[tuple[str, ...], ...],
    *,
    close_as_of: date | None = None,
) -> PlaidClient:
    """The real reader, given one account's holdings reply as plaid-python builds it.

    ``holdings`` is ``(security type, value[, close price])``, each priced at 10
    (cash at 1) at the close of OLDER_SOURCE's day; the close price, when given,
    is Plaid's for the session ``close_as_of``. A hand-built record cannot show
    what the reader makes of a reply, and a stand-in object cannot show what the
    SDK does to one: its holdings accounts refuse their own ``balances``
    (test_plaid_client).
    """
    positions = tuple(
        cash(spec[1])
        if spec[0] == "cash"
        else Position(
            spec[0],
            str(Decimal(spec[1]) / 10),
            "10",
            spec[2] if len(spec) > 2 else None,
            close_as_of if len(spec) > 2 else None,
        )
        for spec in holdings
    )
    return sdk_client(total, positions, priced_on=date(2026, 1, 13))


def sdk_client(total: str, positions: tuple[Position, ...], *, priced_on: date) -> PlaidClient:
    """The real reader over a reply the SDK built: one account, its holdings priced ``priced_on``."""
    body = {
        "accounts": [
            {
                "account_id": "account-portfolio",
                "balances": {
                    "available": None,
                    "current": float(total),
                    "iso_currency_code": "USD",
                    "limit": None,
                    "margin_loan_amount": None,
                    "unofficial_currency_code": None,
                },
                "mask": "0000",
                "name": "Synthetic Brokerage",
                "official_name": None,
                "subtype": "brokerage",
                "type": "investment",
            }
        ],
        "holdings": [
            {
                "account_id": "account-portfolio",
                "cost_basis": None,
                "institution_price": float(position.price),
                "institution_price_as_of": priced_on.isoformat(),
                "institution_price_datetime": None,
                "institution_value": float(Decimal(position.quantity) * Decimal(position.price)),
                "iso_currency_code": "USD",
                "quantity": float(position.quantity),
                "security_id": f"security-{index}",
                "unofficial_currency_code": None,
            }
            for index, position in enumerate(positions)
        ],
        "securities": [_sdk_security(index, position) for index, position in enumerate(positions)],
        "item": {
            "available_products": ["balance"],
            "billed_products": ["investments"],
            "consent_expiration_time": None,
            "error": None,
            "institution_id": "ins_x",
            "item_id": "item-1",
            "update_type": "background",
            "webhook": "",
        },
        "request_id": "req-1",
        "is_investments_fallback_item": False,
    }
    reply = plaid.ApiClient().deserialize(
        SimpleNamespace(data=json.dumps(body)), (InvestmentsHoldingsGetResponse,), True
    )
    return PlaidClient(CREDENTIALS, api=FakeSandboxApi(investments_holdings_get=reply))


def sync_for(
    db: sqlite3.Connection,
    client: FakeClient,
    *,
    mode: BalanceMode,
) -> FullSync:
    db.commit()
    return FullSync(Store(db), client, FakeTokens(), balance_mode=mode)


def test_realtime_balance_keeps_the_call_and_source_clocks_separate(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "balance")
    account_id = add_account(db, item_id, "balance")
    add_run(db, "run-realtime")
    client = FakeClient(db, realtime=(balance("balance"),))

    result = sync_for(db, client, mode=BalanceMode.REALTIME).run("run-realtime", at=NOW)

    assert result.ok
    assert client.calls == [("realtime", "material-balance")]
    assert len(result.observations) == 1
    observed = result.observations[0]
    assert observed.account_id == account_id
    assert observed.figure == SourcedFigure(12_345, "USD", SOURCE, BALANCE_LAST_UPDATED)
    assert observed.fetched_at == NOW
    assert observed.figure.as_of != observed.fetched_at
    assert observed.is_carried_forward is False
    clocks = db.execute(
        "SELECT last_fetch_at, last_source_as_of FROM account WHERE id = ?", (account_id,)
    ).fetchone()
    assert clocks == (_db_time(NOW), _db_time(SOURCE))


def test_realtime_balance_uses_fetched_at_only_under_the_named_f5_exception(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "fallback")
    add_account(db, item_id, "fallback")
    add_run(db, "run-fallback")
    client = FakeClient(db, realtime=(balance("fallback", updated=None),))

    result = sync_for(db, client, mode=BalanceMode.REALTIME).run("run-fallback", at=NOW)

    assert result.observations[0].figure.as_of == NOW
    assert result.observations[0].figure.source_clock == BALANCE_REALTIME_FETCH


def test_cached_mode_calls_accounts_get_and_is_unknown_even_if_a_clock_arrives(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "cached")
    add_account(db, item_id, "cached")
    add_run(db, "run-cached")
    client = FakeClient(db, cached=(balance("cached"),))

    result = sync_for(db, client, mode=BalanceMode.CACHED).run("run-cached", at=NOW)

    assert client.calls == [("cached", "material-cached")]
    assert result.observations[0].figure == SourcedFigure(12_345, "USD", None, UNKNOWN)
    assert result.observations[0].is_carried_forward is False


def test_holdings_value_comes_from_the_account_total_and_clock_from_oldest_holding(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "portfolio", investments_update=NOW)
    add_account(db, item_id, "portfolio", policy=FreshnessPolicy.SYNCED_HOLDINGS)
    add_run(db, "run-holdings")
    investments = InvestmentRecords(
        accounts=(balance("portfolio", "999.99"),),
        holdings=(
            holding("portfolio", "599.996", price_datetime=SOURCE),
            holding("portfolio", "400.00", price_as_of=date(2026, 1, 13)),
        ),
    )
    client = FakeClient(db, investments=investments)

    result = sync_for(db, client, mode=BalanceMode.REALTIME).run("run-holdings", at=NOW)

    observed = result.observations[0]
    assert observed.figure.value_minor == 99_999
    assert observed.figure.value_minor != 100_000, "within rounding, the total is the value"
    assert observed.figure.as_of == OLDER_SOURCE
    assert observed.figure.source_clock == HOLDING_PRICE_AS_OF
    assert observed.source is ObservationSource.PLAID_HOLDINGS


def test_holdings_take_the_older_item_status_clock_and_name_that_evidence(
    db: sqlite3.Connection,
) -> None:
    item_clock = SOURCE - timedelta(days=2)
    item_id = add_item(db, "item-clock", investments_update=item_clock)
    add_account(db, item_id, "item-clock", policy=FreshnessPolicy.SYNCED_HOLDINGS)
    add_run(db, "run-item-clock")
    client = FakeClient(
        db,
        investments=InvestmentRecords(
            accounts=(balance("item-clock"),),
            holdings=(holding("item-clock", "123.45", price_datetime=SOURCE),),
        ),
    )

    result = sync_for(db, client, mode=BalanceMode.REALTIME).run("run-item-clock", at=NOW)

    assert result.observations[0].figure.as_of == item_clock
    assert result.observations[0].figure.source_clock == INVESTMENTS_STATUS_UPDATE


def test_midnight_holding_clock_is_date_granular_market_close_not_midnight(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "midnight", investments_update=NOW)
    add_account(db, item_id, "midnight", policy=FreshnessPolicy.SYNCED_HOLDINGS)
    add_run(db, "run-midnight")
    client = FakeClient(
        db,
        investments=InvestmentRecords(
            accounts=(balance("midnight"),),
            holdings=(
                holding(
                    "midnight",
                    "123.45",
                    price_datetime=datetime(2026, 1, 13, tzinfo=UTC),
                ),
            ),
        ),
    )

    result = sync_for(db, client, mode=BalanceMode.REALTIME).run("run-midnight", at=NOW)

    assert result.observations[0].figure.as_of == OLDER_SOURCE
    assert result.observations[0].figure.source_clock == HOLDING_PRICE_DATETIME


def test_missing_holding_clock_or_item_clock_is_unknown_not_inferred(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "unknown", investments_update=None)
    add_account(db, item_id, "unknown", policy=FreshnessPolicy.SYNCED_HOLDINGS)
    add_run(db, "run-unknown")
    client = FakeClient(
        db,
        investments=InvestmentRecords(
            accounts=(balance("unknown"),),
            holdings=(holding("unknown", "123.45", price_datetime=SOURCE),),
        ),
    )

    result = sync_for(db, client, mode=BalanceMode.REALTIME).run("run-unknown", at=NOW)

    assert result.observations[0].figure == SourcedFigure(12_345, "USD", None, UNKNOWN)


@pytest.mark.parametrize(
    ("total", "holdings", "expected"),
    [
        pytest.param(
            "1000.00",
            (("etf", "700.00", "9.40"), ("etf", "360.00", "9.50"), ("cash", "0")),
            SourcedFigure(106_000, "USD", OLDER_SOURCE, HOLDING_PRICE_AS_OF),
            id="a total behind holdings that list the cash, even at zero, is their value",
        ),
        pytest.param(
            "1000.00",
            (("etf", "700.00"), ("etf", "360.00"), ("cash", "0")),
            SourcedFigure(100_000, "USD", None, UNKNOWN),
            id="unless nothing shows the total is those holdings a session ago",
        ),
        pytest.param(
            "1000.00",
            (("etf", "700.00"), ("etf", "360.00")),
            SourcedFigure(100_000, "USD", None, UNKNOWN),
            id="holdings that list no cash may omit some, so the total stands undated",
        ),
        pytest.param(
            "1000.00",
            (("equity", "600.012"), ("cash", "400.00")),
            SourcedFigure(100_000, "USD", OLDER_SOURCE, HOLDING_PRICE_AS_OF),
            id="a total its holdings add up to, to rounding, is unchanged",
        ),
        pytest.param(
            "1000.00",
            (("etf", "1000.004"),),
            SourcedFigure(100_000, "USD", OLDER_SOURCE, HOLDING_PRICE_AS_OF),
            id="adding up needs no cash holding",
        ),
        pytest.param(
            "0",
            (),
            SourcedFigure(0, "USD", None, UNKNOWN),
            id="an empty account has nothing to date it",
        ),
        pytest.param(
            "1000.00",
            (),
            SourcedFigure(100_000, "USD", None, UNKNOWN),
            id="no holdings are not holdings that list the cash",
        ),
    ],
)
def test_a_holdings_account_is_valued_by_what_its_clock_dates(
    db: sqlite3.Connection,
    total: str,
    holdings: tuple[tuple[str, ...], ...],
    expected: SourcedFigure,
) -> None:
    """The figure and its clock describe the same data (owner's report, 2026-11-23).

    The total came from the reply's balance, which Plaid may cache, and the
    clock from the holdings' prices: a figure a session old, dated today. Each
    reply is read by the real reader from what the SDK built, and every row
    the fix did not mean to move is pinned beside the two it did. The first
    row's total is its holdings at the previous session's close prices, which
    is what lets them be its value (the check below).
    """
    item_id = add_item(db, "portfolio")
    add_account(db, item_id, "portfolio", policy=FreshnessPolicy.SYNCED_HOLDINGS)
    add_run(db, "run-valued")
    db.commit()
    sync = FullSync(
        Store(db),
        sdk_holdings_client(total, holdings, close_as_of=date(2026, 1, 12)),
        FakeTokens(),
        balance_mode=BalanceMode.CACHED,
    )

    result = sync.run("run-valued", at=NOW)

    assert result.ok
    assert result.observations[0].figure == expected


def test_failed_fetch_carries_the_whole_previous_observation_without_advancing_either_clock(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "carry")
    account_id = add_account(db, item_id, "carry")
    add_run(db, "run-before", OLDER_FETCH)
    store = Store(db)
    previous = store.observations.append(
        ObservationDraft(
            sync_run_id="run-before",
            account_id=account_id,
            observed_at=OLDER_FETCH,
            figure=SourcedFigure(11_111, "USD", OLDER_SOURCE, BALANCE_LAST_UPDATED),
            source=ObservationSource.PLAID_BALANCE,
            fetched_at=OLDER_FETCH,
            is_carried_forward=False,
        )
    )
    add_run(db, "run-carry")
    client = FakeClient(db, realtime=RuntimeError("response detail must not escape"))

    result = sync_for(db, client, mode=BalanceMode.REALTIME).run("run-carry", at=NOW)

    assert result.ok is False
    assert result.carried_forward_count == 1
    assert result.missing_count == 0
    carried = result.observations[0]
    assert carried.figure == previous.figure
    assert carried.fetched_at == previous.fetched_at
    assert carried.figure.as_of == OLDER_SOURCE
    assert carried.is_carried_forward is True
    assert result.failures[0].error_type == "RuntimeError"
    assert "response detail" not in repr(result)
    clocks = db.execute(
        "SELECT last_fetch_at, last_source_as_of FROM account WHERE id = ?", (account_id,)
    ).fetchone()
    assert clocks == (None, None)


def test_failure_without_history_is_reported_and_does_not_invent_an_observation(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "first-failure")
    add_account(db, item_id, "first-failure")
    add_run(db, "run-first-failure")
    client = FakeClient(db, realtime=RuntimeError("synthetic"))

    result = sync_for(db, client, mode=BalanceMode.REALTIME).run("run-first-failure", at=NOW)

    assert result.observations == ()
    assert result.carried_forward_count == 0
    assert result.missing_count == 1
    assert db.execute("SELECT count(*) FROM observation").fetchone() == (0,)


def test_one_product_failure_does_not_discard_the_other_product(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "mixed")
    balance_id = add_account(db, item_id, "mixed-balance")
    holdings_id = add_account(
        db,
        item_id,
        "mixed-holdings",
        policy=FreshnessPolicy.SYNCED_HOLDINGS,
    )
    add_run(db, "run-before-mixed", OLDER_FETCH)
    store = Store(db)
    store.observations.append(
        ObservationDraft(
            sync_run_id="run-before-mixed",
            account_id=holdings_id,
            observed_at=OLDER_FETCH,
            figure=SourcedFigure(22_222, "USD", OLDER_SOURCE, HOLDING_PRICE_AS_OF),
            source=ObservationSource.PLAID_HOLDINGS,
            fetched_at=OLDER_FETCH,
            is_carried_forward=False,
        )
    )
    add_run(db, "run-mixed")
    client = FakeClient(
        db,
        realtime=(balance("mixed-balance"),),
        investments=RuntimeError("synthetic holdings failure"),
    )

    result = sync_for(db, client, mode=BalanceMode.REALTIME).run("run-mixed", at=NOW)

    assert client.calls == [
        ("realtime", "material-mixed"),
        ("holdings", "material-mixed"),
    ]
    assert {row.account_id for row in result.observations} == {balance_id, holdings_id}
    assert {row.account_id for row in result.observations if row.is_carried_forward} == {
        holdings_id
    }
    assert result.carried_forward_count == 1


def test_sync_targets_are_discovered_live_and_honor_each_archive_marker(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "targets")
    confirmed = add_account(db, item_id, "confirmed")
    new = add_account(db, item_id, "new", reconciliation="NEW")
    add_account(
        db,
        item_id,
        "state-archived",
        reconciliation="ARCHIVED",
    )
    add_account(db, item_id, "time-archived", archived_at=NOW)
    add_run(db, "run-targets")
    client = FakeClient(
        db,
        realtime=(balance("confirmed"), balance("new")),
    )

    result = sync_for(db, client, mode=BalanceMode.REALTIME).run("run-targets", at=NOW)

    assert result.attempted_count == 2
    assert {row.account_id for row in result.observations} == {confirmed, new}


def test_sync_targets_exclude_manual_policy_independently_of_linkage(
    db: sqlite3.Connection,
) -> None:
    item_id = add_item(db, "policy")
    synced = add_account(db, item_id, "synced")
    add_account(db, item_id, "linked-manual", policy=FreshnessPolicy.MANUAL_STATIC)
    add_run(db, "run-policy")
    client = FakeClient(db, realtime=(balance("synced"),))

    result = sync_for(db, client, mode=BalanceMode.REALTIME).run("run-policy", at=NOW)

    assert result.attempted_count == 1
    assert {row.account_id for row in result.observations} == {synced}


def test_collected_sync_retries_only_persistence_under_immediate_transaction(
    db: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    """A second-row failure rolls back the first; retry cannot re-fetch it."""
    db.commit()
    path = tmp_path / "sync.db"
    with closing(sqlite3.connect(path)) as writer, closing(sqlite3.connect(path)) as observer:
        db.backup(writer)
        migrate(writer)
        observer.execute("PRAGMA busy_timeout = 0")
        first_item = add_item(writer, "first")
        second_item = add_item(writer, "second")
        first = add_account(writer, first_item, "first")
        second = add_account(writer, second_item, "second")
        add_run(writer, "collected")
        writer.commit()

        class ConcurrentWriterClient(FakeClient):
            def fetch_realtime_balances(self, access_token: str) -> tuple[BalanceRecord, ...]:
                # Actual independent WAL writer during each provider call, not
                # an in_transaction flag standing in for lock availability.
                observer.execute("BEGIN IMMEDIATE")
                add_run(observer, f"concurrent-{len(self.calls)}")
                observer.commit()
                return super().fetch_realtime_balances(access_token)

        client = ConcurrentWriterClient(writer, realtime=(balance("first"), balance("second")))
        sync = sync_for(writer, client, mode=BalanceMode.REALTIME)
        plan = sync.collect("collected", at=NOW)
        assert plan.attempted_count == 2
        assert not plan.failures
        assert not writer.in_transaction
        assert observer.execute("SELECT count(*) FROM observation").fetchone() == (0,)
        calls = list(client.calls)
        assert len(calls) == 2

        writer.execute(
            f"CREATE TEMP TRIGGER fail_second BEFORE INSERT ON observation "
            f"WHEN NEW.account_id = {second} BEGIN "
            "SELECT RAISE(ABORT, 'synthetic second row failure'); END"
        )
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.IntegrityError, match="synthetic second row"):
            sync.persist(plan)
        assert writer.execute("SELECT count(*) FROM observation").fetchone() == (1,)
        assert observer.execute("SELECT count(*) FROM observation").fetchone() == (0,)
        writer.rollback()
        assert writer.execute("SELECT count(*) FROM observation").fetchone() == (0,)
        assert writer.execute(
            "SELECT last_fetch_at FROM account WHERE id=?", (first,)
        ).fetchone() == (None,)
        writer.execute("DROP TRIGGER fail_second")
        writer.execute("BEGIN IMMEDIATE")
        result = sync.persist(plan)
        assert result.ok
        assert writer.in_transaction  # persist never commits behind its caller
        assert observer.execute("SELECT count(*) FROM observation").fetchone() == (0,)
        writer.commit()
        assert observer.execute("SELECT count(*) FROM observation").fetchone() == (2,)
        assert client.calls == calls
        assert {row.account_id for row in result.observations} == {first, second}
        # A retry AFTER commit is different; the existing append-only rule
        # continues to reject duplicate observations, even identical ones.
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(ObservationConflictError):
            sync.persist(plan)
        writer.rollback()
        assert client.calls == calls
