"""Task 06: the Sandbox rehearsal, driven entirely by synthetic responses.

No test here makes a live call — task 05's rule, inherited. What the suite cannot
prove is the empirical half of the acceptance criterion (what Sandbox *actually*
returns); that comes from the recorded run and lives in ``DESIGN.md``.

The cycle is exercised **through** :class:`~networth.plaid.client.PlaidClient` rather
than around it, because §5's boundary is part of what is under test: if the rehearsal
ever grew its own SDK client again, these tests would still pass against the fake but
the seam would be gone. The last two tests pin the boundary itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from plaid.exceptions import ApiException

from networth.plaid.client import ExchangedItem, HoldingsObservation, PlaidCallError, PlaidClient
from networth.plaid.environment import PlaidCredentials, PlaidEnvironment, paths_for
from networth.plaid.observation import RecordSet, record_set
from networth.plaid.rehearsal import (
    ACCOUNT_FIELDS,
    HOLDING_FIELDS,
    SANDBOX_PASSWORD,
    SANDBOX_USERNAME,
    SECURITY_FIELDS,
    RehearsalError,
    SandboxRehearsal,
)
from networth.tokenstore import SecretKind, TokenStore, parse_secret_ref
from tests.fake_plaid import ACCESS_TOKEN, INSTITUTION, ITEM_ID, FakeSandboxApi

SANDBOX = PlaidCredentials(
    client_id="synthetic-client-id",
    secret="synthetic-sandbox-secret",
    environment=PlaidEnvironment.SANDBOX,
)
PRODUCTION = PlaidCredentials(
    client_id="synthetic-client-id",
    secret="synthetic-production-secret",
    environment=PlaidEnvironment.PRODUCTION,
)


def _rehearsal(
    tmp_path: Path,
    api: FakeSandboxApi,
    credentials: PlaidCredentials = SANDBOX,
    *,
    store: TokenStore | None = None,
) -> SandboxRehearsal:
    return SandboxRehearsal(
        credentials,
        token_store=store if store is not None else TokenStore(tmp_path / "tokens"),
        database=paths_for(credentials.environment, data_dir=tmp_path).database,
        client=PlaidClient(credentials, api=api),
    )


def test_the_rehearsal_refuses_production_credentials(tmp_path: Path) -> None:
    """The task's one 'must not'. F2a makes a mistake here permanent."""
    with pytest.raises(RehearsalError, match="refuses to run against 'production'"):
        _rehearsal(tmp_path, FakeSandboxApi(), PRODUCTION)


def test_refusing_production_happens_before_any_call_is_made(tmp_path: Path) -> None:
    """Refusing after the first request would already have reached Plaid."""
    api = FakeSandboxApi()
    with pytest.raises(RehearsalError):
        _rehearsal(tmp_path, api, PRODUCTION)
    assert api.requests == []


def test_a_full_cycle_links_exchanges_and_fetches(tmp_path: Path) -> None:
    outcome = _rehearsal(tmp_path, FakeSandboxApi()).run()

    assert outcome.environment is PlaidEnvironment.SANDBOX
    assert outcome.institution_id == INSTITUTION
    assert outcome.item_id == ITEM_ID
    assert (outcome.accounts.count, outcome.holdings.count, outcome.securities.count) == (1, 1, 1)


def test_the_cycle_runs_the_four_calls_in_the_order_production_will(tmp_path: Path) -> None:
    api = FakeSandboxApi()
    _rehearsal(tmp_path, api).run()

    assert api.called == [
        "institutions_get",
        "sandbox_public_token_create",
        "item_public_token_exchange",
        "accounts_balance_get",
        "investments_holdings_get",
    ]


def test_the_sandbox_link_uses_user_good_and_pass_good(tmp_path: Path) -> None:
    """The acceptance criterion names the test user; assert it rather than assume it."""
    api = FakeSandboxApi()
    _rehearsal(tmp_path, api).run()

    request = api.request_for("sandbox_public_token_create")
    assert request.options.override_username == SANDBOX_USERNAME == "user_good"
    assert request.options.override_password == SANDBOX_PASSWORD == "pass_good"


def test_no_institution_is_named_by_this_program(tmp_path: Path) -> None:
    """AGENTS.md rule 0: the institution is discovered, never asserted."""
    api = FakeSandboxApi()
    _rehearsal(tmp_path, api).run()

    asked = api.request_for("institutions_get")
    assert [str(product.value) for product in asked.options.products] == ["investments"]
    used = api.request_for("sandbox_public_token_create")
    assert used.institution_id == INSTITUTION  # from the response, not from a constant


def test_the_access_token_is_durable_before_the_outcome_is_returned(tmp_path: Path) -> None:
    """§14a's ordering: material lands durably, then the caller records an item."""
    store = TokenStore(tmp_path / "tokens")

    outcome = _rehearsal(tmp_path, FakeSandboxApi(), store=store).run()

    kind, _ = parse_secret_ref(outcome.secret_ref)
    assert kind is SecretKind.ACCESS_TOKEN
    assert store.get(outcome.secret_ref).reveal() == ACCESS_TOKEN
    assert store.record(outcome.secret_ref).item_id == ITEM_ID


def test_the_rehearsal_writes_only_into_the_sandbox_database(tmp_path: Path) -> None:
    """Criterion 3, from this side: the database travels with the environment."""
    outcome = _rehearsal(tmp_path, FakeSandboxApi()).run()

    assert outcome.database == paths_for(PlaidEnvironment.SANDBOX, data_dir=tmp_path).database
    assert outcome.database.name == "networth-sandbox.db"
    assert outcome.database != paths_for(PlaidEnvironment.PRODUCTION, data_dir=tmp_path).database


def test_observations_record_the_type_that_decides_whether_money_is_a_float(
    tmp_path: Path,
) -> None:
    """§7 forbids float money, so the type is the finding — never the amount."""
    outcome = _rehearsal(tmp_path, FakeSandboxApi()).run()

    balances = {o.path: o for o in outcome.accounts.fields}
    assert balances["balances.current"].type_name == "float"
    assert balances["balances.available"].note == "null"


def test_every_source_clock_section_8_1_names_is_asked_for(tmp_path: Path) -> None:
    """§8.1's table names four clocks; observing two of them is not observing it.

    The two below are the ones §8.1 reaches for *first* — it prefers
    ``institution_price_datetime`` over ``institution_price_as_of``, and a realtime
    cash balance has no other clock than ``balances.last_updated_datetime``. Both are
    documented by Plaid as select-institution only, which is exactly why a rehearsal
    has to look: their absence is a finding for tasks ``12`` and ``14``, and a report
    that never asked would have shown a complete source clock instead.
    """
    asked = set(ACCOUNT_FIELDS) | set(HOLDING_FIELDS) | set(SECURITY_FIELDS)

    assert {
        "balances.last_updated_datetime",
        "institution_price_datetime",
        "institution_price_as_of",
        "close_price_as_of",
    } <= asked


def test_the_preferred_source_clocks_are_reported_when_a_response_supplies_them(
    tmp_path: Path,
) -> None:
    """Asking is half of it: the observation has to reach the report as present.

    Distinguishes the two answers the live run exists to tell apart — ``absent``
    (Plaid does not supply the field) from a real type. At the head codex reviewed,
    neither path was requested, so neither observation existed at all.
    """
    outcome = _rehearsal(tmp_path, FakeSandboxApi()).run()

    accounts = {o.path: o for o in outcome.accounts.fields}
    holdings = {o.path: o for o in outcome.holdings.fields}

    assert accounts["balances.last_updated_datetime"].note == "datetime"
    assert holdings["institution_price_datetime"].note == "datetime"
    # The fallbacks stay observable in their own right: §8.1 uses `*_as_of` only when
    # the preferred clock is missing, so "which one answered" is itself the finding.
    assert holdings["institution_price_as_of"].note == "null"


def test_the_outcome_never_renders_an_item_id_an_institution_or_a_secret_ref(
    tmp_path: Path,
) -> None:
    """This object is what a traceback prints and what a report is tempted to print."""
    outcome = _rehearsal(tmp_path, FakeSandboxApi()).run()

    rendered = repr(outcome)

    assert INSTITUTION not in rendered
    assert ITEM_ID not in rendered
    assert outcome.secret_ref not in rendered
    assert "sandbox" in rendered  # the part that makes a stray repr diagnosable


def test_a_plaid_error_never_carries_the_response_body_into_the_message(
    tmp_path: Path,
) -> None:
    """The same exception type is raised by the calls that carry the credential."""
    exc = ApiException(status=400, reason="Bad Request")
    exc.body = '{"error_code":"INVALID_FIELD","secret":"never-print-me"}'
    api = FakeSandboxApi(institutions_get=exc)

    with pytest.raises(PlaidCallError) as raised:
        _rehearsal(tmp_path, api).run()

    message = str(raised.value)
    assert "institutions/get failed" in message
    assert "HTTP 400" in message
    assert "never-print-me" not in message
    assert "INVALID_FIELD" not in message


def test_a_transport_failure_is_reported_without_inventing_a_result(tmp_path: Path) -> None:
    api = FakeSandboxApi(accounts_balance_get=OSError("connection reset"))

    with pytest.raises(PlaidCallError, match="accounts/balance/get failed: OSError"):
        _rehearsal(tmp_path, api).run()


def test_an_institution_list_with_no_match_stops_rather_than_guessing(tmp_path: Path) -> None:
    api = FakeSandboxApi(institutions_get=SimpleNamespace(institutions=[]))

    with pytest.raises(PlaidCallError, match="no institution supporting investments"):
        _rehearsal(tmp_path, api).run()


def test_an_exchange_missing_its_item_id_is_a_failure_not_a_half_recorded_item(
    tmp_path: Path,
) -> None:
    """A stored token whose item_id is unknown is the orphan §14a works to avoid."""
    api = FakeSandboxApi(
        item_public_token_exchange=SimpleNamespace(access_token="access-x", item_id=None)
    )
    store = TokenStore(tmp_path / "tokens")

    with pytest.raises(PlaidCallError, match="no access_token or no item_id"):
        _rehearsal(tmp_path, api, store=store).run()

    assert list(store.directory.glob("*")) == []


class NarrowClient:
    """A client that implements the rehearsal's protocol and nothing else.

    The point of the type: this object has no ``item_get``, no SDK, and no way to
    reach an endpoint the protocol did not declare — so if the rehearsal ever calls
    something outside it, this fails with ``AttributeError`` rather than quietly
    working. It is also what proves task 05's fakes never need Link methods.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def first_institution_supporting(
        self, *, products: Sequence[str], country_codes: Sequence[str]
    ) -> str:
        self.calls.append("first_institution_supporting")
        return INSTITUTION

    def sandbox_public_token_create(
        self, *, institution_id: str, products: Sequence[str], username: str, password: str
    ) -> str:
        self.calls.append("sandbox_public_token_create")
        return "public-token"

    def item_public_token_exchange(self, public_token: str) -> ExchangedItem:
        self.calls.append("item_public_token_exchange")
        return ExchangedItem(access_token=ACCESS_TOKEN, item_id=ITEM_ID)

    def accounts_balance_get(self, access_token: str, *, fields: Sequence[str]) -> RecordSet:
        self.calls.append("accounts_balance_get")
        return record_set("accounts", [SimpleNamespace(account_id="a")], fields)

    def investments_holdings_get(
        self, access_token: str, *, holding_fields: Sequence[str], security_fields: Sequence[str]
    ) -> HoldingsObservation:
        self.calls.append("investments_holdings_get")
        return HoldingsObservation(
            holdings=record_set("holdings", [], holding_fields),
            securities=record_set("securities", [], security_fields),
        )


def test_the_rehearsal_needs_nothing_of_a_client_beyond_its_own_protocol(
    tmp_path: Path,
) -> None:
    client = NarrowClient()
    rehearsal = SandboxRehearsal(
        SANDBOX,
        token_store=TokenStore(tmp_path / "tokens"),
        database=paths_for(PlaidEnvironment.SANDBOX, data_dir=tmp_path).database,
        client=client,
    )

    outcome = rehearsal.run()

    assert client.calls == [
        "first_institution_supporting",
        "sandbox_public_token_create",
        "item_public_token_exchange",
        "accounts_balance_get",
        "investments_holdings_get",
    ]
    assert outcome.accounts.count == 1


def test_no_raw_sdk_object_reaches_the_rehearsal(tmp_path: Path) -> None:
    """§5's boundary, asserted as a property of what crosses it.

    Every field of the outcome is a type this project defines or a builtin. A raw
    ``plaid`` model arriving here would mean the SDK had escaped ``client.py``.
    """
    outcome = _rehearsal(tmp_path, FakeSandboxApi()).run()

    for value in (outcome.accounts, outcome.holdings, outcome.securities):
        assert type(value).__module__.startswith("networth.")
    for field in outcome.accounts.fields:
        assert type(field).__module__.startswith("networth.")


def test_the_exchanged_item_never_renders_its_token(tmp_path: Path) -> None:
    """The default dataclass repr would put an access_token in any traceback."""
    item = ExchangedItem(access_token=ACCESS_TOKEN, item_id=ITEM_ID)

    assert ACCESS_TOKEN not in repr(item)
    assert ITEM_ID not in repr(item)
    assert item.access_token == ACCESS_TOKEN


def test_the_client_this_rehearsal_builds_by_default_is_the_project_client(
    tmp_path: Path,
) -> None:
    """No `client=` argument: the default must be the one SDK-owning seam (§5)."""
    rehearsal = SandboxRehearsal(
        SANDBOX,
        token_store=TokenStore(tmp_path / "tokens"),
        database=paths_for(PlaidEnvironment.SANDBOX, data_dir=tmp_path).database,
    )

    assert isinstance(rehearsal._client, PlaidClient)
