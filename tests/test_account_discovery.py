"""Task 08: a linked Item's accounts become rows, and those rows get fetched.

The last test in this file is the one the module exists for. Everything above
it pins a rule; that one starts from what Link finalization actually leaves
behind — an ``item`` row and nothing else — and follows it to a published total.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from networth import runtime
from networth.account_discovery import AccountDiscovery, DiscoveryResult
from networth.filelock import LockUnavailable, exclusive_file_lock
from networth.full_cycle import FullCycleDispatcher
from networth.payload import open_payload
from networth.plaid import AccountDescriptor, BalanceRecord, HoldingRecord, InvestmentRecords
from networth.plaid.client import ItemStatus, PlaidCallError, PlaidClient
from networth.plaid.environment import PlaidCredentials, PlaidEnvironment
from networth.plaid.errors import ItemState
from networth.publisher import Publisher
from networth.scheduling import FullSyncSchedule
from networth.storage import migrate
from networth.store import Store
from networth.sync import BalanceMode
from networth.tokenstore import TokenStore
from tests.fake_plaid import FakeSandboxApi
from tests.test_item_health import item_status
from tests.test_manual_quotes import Quotes
from tests.test_sync import FakeTokens, add_account, add_item

# A Friday, one hour after the close: the instant a full sync becomes market-due.
READY = datetime(2026, 9, 18, 21, tzinfo=UTC)
CREDENTIALS = PlaidCredentials("synthetic-client", "synthetic-secret", PlaidEnvironment.SANDBOX)


def descriptor(
    suffix: str,
    *,
    type: str = "investment",  # noqa: A002 - the provider's own field name
    currency: str | None = "USD",
) -> AccountDescriptor:
    return AccountDescriptor(
        account_id=f"account-{suffix}",
        name=f"Synthetic {suffix}",
        official_name=None,
        mask="0000",
        type=type,
        subtype=None,
        currency=currency,
    )


class Client:
    """Answers by access token, and records that no write was open when asked."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self.accounts: dict[str, tuple[AccountDescriptor, ...] | Exception] = {}
        self.balances: dict[str, Decimal] = {}
        self.calls: list[tuple[str, str]] = []

    def _asked(self, name: str, token: str) -> None:
        assert not self._db.in_transaction, "provider calls must precede every write"
        self.calls.append((name, token))

    def item_get(self, access_token: str) -> ItemStatus:
        self._asked("item_get", access_token)
        return item_status(access_token.removeprefix("material-"), ItemState.HEALTHY)

    def list_accounts(self, access_token: str) -> tuple[AccountDescriptor, ...]:
        self._asked("list_accounts", access_token)
        answer = self.accounts[access_token]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def _balance_records(self, access_token: str) -> tuple[BalanceRecord, ...]:
        answer = self.accounts[access_token]
        assert not isinstance(answer, Exception)
        return tuple(
            BalanceRecord(d.account_id, self.balances[d.account_id], "USD", None)
            for d in answer
            if d.account_id in self.balances
        )

    def fetch_cached_balances(self, access_token: str) -> tuple[BalanceRecord, ...]:
        self._asked("fetch_cached_balances", access_token)
        return self._balance_records(access_token)

    def fetch_realtime_balances(self, access_token: str) -> tuple[BalanceRecord, ...]:
        raise AssertionError("the runtime selects cached balances")

    def fetch_holdings(self, access_token: str) -> InvestmentRecords:
        self._asked("fetch_holdings", access_token)
        records = self._balance_records(access_token)
        return InvestmentRecords(
            accounts=records,
            holdings=tuple(
                HoldingRecord(r.account_id, r.current, "USD", None, date(2026, 9, 18))
                for r in records
            ),
        )


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(tmp_path / "discovery.db")
    migrate(connection)
    connection.execute(
        "INSERT INTO institution(plaid_institution_id, name, is_oauth) "
        "VALUES ('synthetic-institution', 'Synthetic institution', 0)"
    )
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


def discovery(
    db: sqlite3.Connection, tmp_path: Path, client: Client, *, at: datetime = READY
) -> AccountDiscovery:
    return AccountDiscovery(
        db, client, FakeTokens(), lock_path=tmp_path / "sync.lock", clock=lambda: at
    )


def rows(db: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return db.execute(
        "SELECT plaid_account_id, type, currency, sign, freshness_policy, "
        "include_in_net_worth, reconciliation_state FROM account ORDER BY id"
    ).fetchall()


# --- the client: what an account is, never what it holds ---------------------


def account_record(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "account_id": "acct-1",
        "name": "Synthetic Brokerage",
        "official_name": "Synthetic Official Name",
        "mask": "0000",
        "type": SimpleNamespace(value="investment"),
        "subtype": SimpleNamespace(value="brokerage"),
        "balances": SimpleNamespace(current=1000.0, iso_currency_code="USD"),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def listed(*records: SimpleNamespace) -> tuple[AccountDescriptor, ...]:
    api = FakeSandboxApi(accounts_get=SimpleNamespace(accounts=list(records)))
    listing = PlaidClient(CREDENTIALS, api=api).list_accounts("synthetic-access")
    assert api.called == ["accounts_get"]
    assert api.request_for("accounts_get").access_token == "synthetic-access"
    return listing


def test_list_accounts_reads_identity_and_category_and_renders_neither_id_nor_name() -> None:
    (account,) = listed(account_record())

    assert account == AccountDescriptor(
        account_id="acct-1",
        name="Synthetic Brokerage",
        official_name="Synthetic Official Name",
        mask="0000",
        type="investment",
        subtype="brokerage",
        currency="USD",
    )
    rendered = repr(account)
    assert "acct-1" not in rendered and "Synthetic" not in rendered
    assert "investment" in rendered and "USD" in rendered


def test_an_account_plaid_cannot_denominate_or_value_is_still_listed() -> None:
    """One such account must not hide every other account behind the same login."""
    undenominated, unvalued, bare = listed(
        account_record(balances=SimpleNamespace(current=5.0, iso_currency_code=None)),
        account_record(account_id="acct-2", balances=SimpleNamespace(current=None)),
        account_record(
            account_id="acct-3", balances=None, official_name=None, mask="", subtype=None
        ),
    )

    assert undenominated.currency is None
    assert unvalued.currency is None
    assert (bare.currency, bare.official_name, bare.mask, bare.subtype) == (None, None, None, None)


def test_an_institutions_padding_is_trimmed_and_an_empty_name_falls_back() -> None:
    padded, blank_with_official, blank = listed(
        account_record(name="  Padded  "),
        account_record(account_id="acct-2", name="   "),
        account_record(account_id="acct-3", name="   ", official_name=None),
    )

    assert padded.name == "Padded"
    assert blank_with_official.name == "Synthetic Official Name"
    assert blank.name == "Account"


@pytest.mark.parametrize("field", ["account_id", "type"])
def test_a_record_without_identity_or_category_is_a_failed_call(field: str) -> None:
    with pytest.raises(
        PlaidCallError, match=rf"accounts/get returned a record with no usable {field}"
    ):
        listed(account_record(**{field: None}))


# --- discovery: which rows exist afterwards ----------------------------------


def test_investment_and_cash_accounts_become_confirmed_asset_rows(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    add_item(db, "one")
    db.commit()
    client = Client(db)
    client.accounts["material-one"] = (
        descriptor("brokerage"),
        descriptor("cash", type="depository"),
    )

    result = discovery(db, tmp_path, client).run_due()

    assert result == DiscoveryResult(items_due=1, items_discovered=1, accounts_created=2)
    assert result.ok
    assert rows(db) == [
        ("account-brokerage", "investment", "USD", 1, "SYNCED_HOLDINGS", 1, "CONFIRMED"),
        ("account-cash", "depository", "USD", 1, "SYNCED_BALANCE", 1, "CONFIRMED"),
    ]
    assert db.execute(
        "SELECT name, mask, created_at, lineage_id = id FROM account WHERE id = 1"
    ).fetchone() == ("Synthetic brokerage", "0000", "2026-09-18T21:00:00.000000Z", 1)
    assert db.execute("SELECT accounts_discovered_at FROM item").fetchone() == (
        "2026-09-18T21:00:00.000000Z",
    )
    assert [a.plaid_account_id for a in Store(db).accounts.syncable()] == [
        "account-brokerage",
        "account-cash",
    ]
    assert not db.in_transaction


@pytest.mark.parametrize(
    "left_out",
    [
        descriptor("card", type="credit"),
        descriptor("mortgage", type="loan"),
        descriptor("misc", type="other"),
        descriptor("foreign", currency="EUR"),
        descriptor("undenominated", currency=None),
    ],
    ids=["credit", "loan", "other", "non-usd", "no-currency"],
)
def test_what_v0_does_not_model_is_counted_and_never_becomes_a_row(
    db: sqlite3.Connection, tmp_path: Path, left_out: AccountDescriptor
) -> None:
    add_item(db, "one")
    db.commit()
    client = Client(db)
    client.accounts["material-one"] = (descriptor("brokerage"), left_out)

    result = discovery(db, tmp_path, client).run_due()

    assert (result.accounts_created, result.accounts_left_out) == (1, 1)
    assert [row[0] for row in rows(db)] == ["account-brokerage"]


def test_an_item_with_nothing_v0_models_is_asked_once_and_not_again(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """The stored answer is what separates "asked, none" from "never asked"."""
    add_item(db, "one")
    db.commit()
    client = Client(db)
    client.accounts["material-one"] = (descriptor("card", type="credit"),)

    first = discovery(db, tmp_path, client).run_due()
    second = discovery(db, tmp_path, client).run_due()

    assert first == DiscoveryResult(items_due=1, items_discovered=1, accounts_left_out=1)
    assert second == DiscoveryResult()
    assert client.calls == [("list_accounts", "material-one")]
    assert rows(db) == []


def test_a_failed_item_stays_unasked_and_does_not_block_the_next_one(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    add_item(db, "one")
    add_item(db, "two")
    db.commit()
    client = Client(db)
    client.accounts["material-one"] = RuntimeError("synthetic-private-provider-detail")
    client.accounts["material-two"] = (descriptor("two"),)

    result = discovery(db, tmp_path, client).run_due()

    assert result == DiscoveryResult(
        items_due=2, items_discovered=1, items_failed=1, accounts_created=1
    )
    assert not result.ok
    assert "synthetic-private-provider-detail" not in repr(result)
    assert db.execute("SELECT accounts_discovered_at IS NULL FROM item ORDER BY id").fetchall() == [
        (1,),
        (0,),
    ]

    client.accounts["material-one"] = (descriptor("one"),)
    retried = discovery(db, tmp_path, client).run_due()

    assert retried == DiscoveryResult(items_due=1, items_discovered=1, accounts_created=1)
    assert [call for call in client.calls if call[1] == "material-two"] == [
        ("list_accounts", "material-two")
    ]


def test_an_unreadable_credential_is_a_failed_item_not_a_crash(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    add_item(db, "one")
    db.commit()

    class Unreadable:
        def get(self, secret_ref: str) -> Any:
            raise RuntimeError("synthetic-private-token-store-detail")

    result = AccountDiscovery(
        db, Client(db), Unreadable(), lock_path=tmp_path / "sync.lock", clock=lambda: READY
    ).run_due()

    assert result == DiscoveryResult(items_due=1, items_failed=1)
    assert db.execute("SELECT accounts_discovered_at FROM item").fetchone() == (None,)


@pytest.mark.parametrize("status", ["NEEDS_REAUTH", "REVOKED"])
def test_an_item_that_cannot_answer_is_not_asked(
    db: sqlite3.Connection, tmp_path: Path, status: str
) -> None:
    add_item(db, "one")
    db.execute("UPDATE item SET status = ?", (status,))
    db.commit()
    client = Client(db)

    assert discovery(db, tmp_path, client).run_due() == DiscoveryResult()
    assert client.calls == []


def test_a_just_linked_item_is_asked_while_still_degraded(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Finalization commits a new Item as DEGRADED; waiting for a poll would stall it."""
    add_item(db, "one")
    db.execute("UPDATE item SET status = 'DEGRADED'")
    db.commit()
    client = Client(db)
    client.accounts["material-one"] = (descriptor("one"),)

    assert discovery(db, tmp_path, client).run_due().accounts_created == 1


def test_an_account_already_on_file_is_neither_duplicated_nor_counted(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    add_account(db, add_item(db, "one"), "existing")
    db.commit()
    client = Client(db)
    client.accounts["material-one"] = (descriptor("existing"), descriptor("added"))

    result = discovery(db, tmp_path, client).run_due()

    assert result.accounts_created == 1
    assert [row[0] for row in rows(db)] == ["account-existing", "account-added"]
    # The pre-existing row is the fixture's, untouched: still its own type.
    assert db.execute("SELECT type FROM account WHERE id = 1").fetchone() == ("synthetic",)


def test_new_accounts_wait_at_new_when_an_archived_predecessor_could_be_double_counted(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    old = add_item(db, "old")
    add_account(db, old, "archived", reconciliation="ARCHIVED", archived_at=READY)
    db.execute("UPDATE item SET accounts_discovered_at = ? WHERE id = ?", (READY.isoformat(), old))
    add_item(db, "new")
    db.commit()
    client = Client(db)
    client.accounts["material-new"] = (descriptor("replacement"),)

    result = discovery(db, tmp_path, client).run_due()

    assert (result.accounts_created, result.accounts_pending) == (1, 1)
    assert rows(db)[-1][-1] == "NEW"


def test_a_replacement_items_accounts_wait_at_new(db: sqlite3.Connection, tmp_path: Path) -> None:
    old = add_item(db, "old")
    db.execute("UPDATE item SET status = 'REVOKED' WHERE id = ?", (old,))
    new = add_item(db, "new")
    db.execute("UPDATE item SET replaces_item_id = ? WHERE id = ?", (old, new))
    db.commit()
    client = Client(db)
    client.accounts["material-new"] = (descriptor("replacement"),)

    result = discovery(db, tmp_path, client).run_due()

    assert (result.accounts_created, result.accounts_pending) == (1, 1)
    assert rows(db) == [
        ("account-replacement", "investment", "USD", 1, "SYNCED_HOLDINGS", 1, "NEW")
    ]


def test_discovery_refuses_an_open_transaction_and_yields_to_the_sync_lock(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    add_item(db, "one")
    db.commit()
    client = Client(db)
    client.accounts["material-one"] = (descriptor("one"),)
    runner = discovery(db, tmp_path, client)

    db.execute("BEGIN")
    with pytest.raises(ValueError, match="no active transaction"):
        runner.run_due()
    db.rollback()

    with exclusive_file_lock(tmp_path / "sync.lock"), pytest.raises(LockUnavailable):
        AccountDiscovery(
            sqlite3.connect(tmp_path / "discovery.db"),
            client,
            FakeTokens(),
            lock_path=tmp_path / "sync.lock",
        ).run_due()
    assert client.calls == []


# --- the schedule: a new account is fetched now, not in twenty hours ---------


def successful_run(db: sqlite3.Connection, at: datetime) -> None:
    db.execute(
        'INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok, kind) '
        "VALUES (lower(hex(randomblob(16))), ?, ?, 'TEST', 1, 'FULL_SYNC')",
        (at.isoformat(), at.isoformat()),
    )


def account_created(db: sqlite3.Connection, at: datetime, **overrides: Any) -> None:
    item = add_item(db, f"at-{at.timestamp()}")
    account = add_account(db, item, f"at-{at.timestamp()}", **overrides)
    db.execute("UPDATE account SET created_at = ? WHERE id = ?", (at.isoformat(), account))


@pytest.mark.parametrize(
    ("created_after", "due"),
    [(timedelta(microseconds=1), True), (timedelta(0), False), (timedelta(minutes=-5), False)],
    ids=["after-the-run-started", "the-same-instant", "before-the-run-started"],
)
def test_an_account_created_after_the_last_successful_start_makes_the_sync_due(
    db: sqlite3.Connection, created_after: timedelta, due: bool
) -> None:
    successful_run(db, READY)
    account_created(db, READY + created_after)
    db.commit()

    status = FullSyncSchedule(db).due(at=READY + timedelta(minutes=10))

    assert (status.market_due, status.elapsed_due) == (False, False)
    assert status.new_account_due is due
    assert status.due is due


def test_a_successful_run_started_after_the_account_ends_that_reason(
    db: sqlite3.Connection,
) -> None:
    successful_run(db, READY)
    account_created(db, READY + timedelta(minutes=5))
    successful_run(db, READY + timedelta(minutes=6))
    db.commit()

    assert not FullSyncSchedule(db).due(at=READY + timedelta(minutes=10)).due


@pytest.mark.parametrize(
    "overrides",
    [{"reconciliation": "ARCHIVED"}, {"archived_at": READY}],
    ids=["archived-state", "archived-stamp"],
)
def test_an_account_no_sync_would_fetch_does_not_make_one_due(
    db: sqlite3.Connection, overrides: dict[str, Any]
) -> None:
    successful_run(db, READY)
    account_created(db, READY + timedelta(minutes=5), **overrides)
    db.commit()

    assert not FullSyncSchedule(db).due(at=READY + timedelta(minutes=10)).due


def test_a_malformed_account_clock_is_ignored_rather_than_made_permanently_due(
    db: sqlite3.Connection,
) -> None:
    successful_run(db, READY)
    account_created(db, READY + timedelta(minutes=5))
    db.execute("UPDATE account SET created_at = 'not a timestamp'")
    db.commit()

    assert not FullSyncSchedule(db).due(at=READY + timedelta(minutes=10)).due


# --- the chain: from what a Link leaves behind to a published total ----------


def test_a_linked_item_with_no_account_rows_ends_as_a_published_total(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Start where ``finalize_durable_result`` stops, and end on the wire.

    The daemon has been running with nothing linked, so its empty full sync has
    already succeeded and — by the market and 20-hour rules alone — is not due
    again until tomorrow. Then an Item appears. Without discovery it has no
    accounts to fetch; without the third due reason they are not fetched today.
    """
    lock = tmp_path / "sync.lock"
    client = Client(db)

    def cycle(at: datetime) -> Any:
        return FullCycleDispatcher(
            db,
            client,
            FakeTokens(),
            Quotes(),
            balance_mode=BalanceMode.CACHED,
            lock_path=lock,
            clock=lambda: at,
            sleep=lambda _: None,
        ).run_due()

    # 1. Nothing linked: the cycle completes over zero accounts and is satisfied.
    assert cycle(READY).state == "COMPLETED"
    assert cycle(READY + timedelta(minutes=5)).state == "NOT_DUE"

    # 2. A Link finalizes: an item row, DEGRADED, and no account row at all.
    linked_at = READY + timedelta(minutes=6)
    add_item(db, "one", investments_update=READY)
    db.execute("UPDATE item SET status = 'DEGRADED'")
    db.commit()
    assert Store(db).accounts.syncable() == ()
    assert cycle(linked_at).state == "NOT_DUE", "without discovery the Item is invisible"

    # 3. Discovery, then the same cycle the runtime runs right after it.
    client.accounts["material-one"] = (
        descriptor("brokerage"),
        descriptor("cash", type="depository"),
        descriptor("card", type="credit"),
    )
    client.balances = {
        "account-brokerage": Decimal("1000.25"),
        "account-cash": Decimal("234.50"),
        "account-card": Decimal("99.00"),
    }
    found = AccountDiscovery(
        db, client, FakeTokens(), lock_path=lock, clock=lambda: linked_at
    ).run_due()
    assert (found.accounts_created, found.accounts_left_out) == (2, 1)

    result = cycle(linked_at + timedelta(minutes=1))

    assert result.state == "COMPLETED" and result.ok and result.run_id is not None
    snapshot = Store(db).snapshots.for_sync_run(result.run_id)
    assert snapshot is not None
    assert snapshot.net_worth.value_minor == 100_025 + 23_450  # the card is not in it
    assert snapshot.counts.account_count == 2
    assert snapshot.counts.unreconciled_account_count == 0
    assert cycle(linked_at + timedelta(minutes=2)).state == "NOT_DUE"

    # 4. And it reaches the phone's wire format under the active pairing.
    db.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) "
        "VALUES ('00000000-0000-4000-8000-000000000008', ?, 'synthetic', 'ACTIVE')",
        (READY.isoformat(),),
    )
    db.commit()
    key = bytes(range(32))
    published = Publisher(db, lambda _: key).publish(at=linked_at + timedelta(minutes=3))
    payload = json.loads(open_payload(published.envelope, key))

    assert payload["total"]["value_minor"] == 123_475
    assert payload["total"]["account_count"] == 2
    assert len(payload["accounts"]) == 2


def test_one_runtime_sync_activation_takes_a_new_item_from_no_accounts_to_a_snapshot(
    db: sqlite3.Connection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same chain through the job the installed unit runs, in one activation.

    ``networth-sync.service`` is ``runtime.sync``. If discovery were not its first
    job, the full cycle in the same activation would find nothing to fetch, mark
    itself satisfied, and the accounts would wait for the next one.
    """
    add_item(db, "one", investments_update=READY)
    db.execute("UPDATE item SET status = 'DEGRADED'")
    db.commit()
    client = Client(db)
    client.accounts["material-one"] = (
        descriptor("brokerage"),
        descriptor("cash", type="depository"),
    )
    client.balances = {"account-brokerage": Decimal("10.00"), "account-cash": Decimal("2.50")}

    ok = runtime.sync(
        db, cast(PlaidClient, client), cast(TokenStore, FakeTokens()), tmp_path / "sync.lock"
    )

    assert ok
    assert capsys.readouterr().out.splitlines() == [
        "accounts: completed",
        "health: completed",
        "full: completed",
        "quotes: completed",
    ]
    assert [call[0] for call in client.calls] == [
        "list_accounts",
        "item_get",
        "fetch_cached_balances",
        "fetch_holdings",
    ]
    assert db.execute("SELECT count(*) FROM account").fetchone() == (2,)
    assert db.execute(
        "SELECT total_net_worth_minor FROM snapshot ORDER BY id DESC LIMIT 1"
    ).fetchone() == (1_250,)


def test_a_failed_discovery_fails_the_activation_without_stopping_the_other_jobs(
    db: sqlite3.Connection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_item(db, "one")
    db.commit()
    client = Client(db)
    client.accounts["material-one"] = RuntimeError("synthetic-private-provider-detail")

    ok = runtime.sync(
        db, cast(PlaidClient, client), cast(TokenStore, FakeTokens()), tmp_path / "sync.lock"
    )

    out = capsys.readouterr()
    assert not ok
    assert out.out.splitlines() == [
        "accounts: completed",
        "health: completed",
        "full: completed",
        "quotes: completed",
    ]
    assert "synthetic-private-provider-detail" not in out.out + out.err
    assert db.execute("SELECT accounts_discovered_at FROM item").fetchone() == (None,)
