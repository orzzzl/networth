"""Task 06: the Sandbox rehearsal, driven entirely by synthetic responses.

No test here makes a live call — task 05's rule, inherited. What the suite cannot
prove is the empirical half of the acceptance criterion (what Sandbox *actually*
returns); that comes from the recorded run and lives in ``DESIGN.md``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from plaid.exceptions import ApiException

from networth.plaid.environment import PlaidCredentials, PlaidEnvironment, paths_for
from networth.plaid.rehearsal import (
    SANDBOX_PASSWORD,
    SANDBOX_USERNAME,
    FieldObservation,
    RehearsalError,
    SandboxRehearsal,
    _observe,
)
from networth.tokenstore import SecretKind, TokenStore, parse_secret_ref

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

INSTITUTION = "ins_synthetic_0"


class FakeApi:
    """Every call the rehearsal makes, recorded, with synthetic answers."""

    def __init__(self, **overrides: Any) -> None:
        self.requests: list[tuple[str, Any]] = []
        self._overrides = overrides

    def _answer(self, name: str, default: Any, request: Any) -> Any:
        self.requests.append((name, request))
        override = self._overrides.get(name, default)
        if isinstance(override, Exception):
            raise override
        return override

    def institutions_get(self, institutions_get_request: Any) -> Any:
        return self._answer(
            "institutions_get",
            SimpleNamespace(institutions=[SimpleNamespace(institution_id=INSTITUTION)]),
            institutions_get_request,
        )

    def sandbox_public_token_create(self, sandbox_public_token_create_request: Any) -> Any:
        return self._answer(
            "sandbox_public_token_create",
            SimpleNamespace(public_token="public-sandbox-synthetic"),
            sandbox_public_token_create_request,
        )

    def item_public_token_exchange(self, item_public_token_exchange_request: Any) -> Any:
        return self._answer(
            "item_public_token_exchange",
            SimpleNamespace(access_token="access-sandbox-synthetic", item_id="item-synthetic"),
            item_public_token_exchange_request,
        )

    def accounts_balance_get(self, accounts_balance_get_request: Any) -> Any:
        return self._answer(
            "accounts_balance_get",
            SimpleNamespace(
                accounts=[
                    SimpleNamespace(
                        account_id="acct-1",
                        name="Synthetic Brokerage",
                        type="investment",
                        subtype="brokerage",
                        balances=SimpleNamespace(
                            current=1000.0,
                            available=None,
                            limit=None,
                            iso_currency_code="USD",
                            unofficial_currency_code=None,
                        ),
                    )
                ]
            ),
            accounts_balance_get_request,
        )

    def investments_holdings_get(self, investments_holdings_get_request: Any) -> Any:
        return self._answer(
            "investments_holdings_get",
            SimpleNamespace(
                holdings=[
                    SimpleNamespace(
                        account_id="acct-1",
                        security_id="sec-1",
                        quantity=10.0,
                        institution_price=100.0,
                        institution_value=1000.0,
                        cost_basis=900.0,
                        iso_currency_code="USD",
                        institution_price_as_of=None,
                    )
                ],
                securities=[
                    SimpleNamespace(
                        security_id="sec-1",
                        ticker_symbol="SYN",
                        name="Synthetic Fund",
                        type="etf",
                        close_price=100.0,
                        close_price_as_of=None,
                        iso_currency_code="USD",
                        is_cash_equivalent=False,
                    )
                ],
            ),
            investments_holdings_get_request,
        )


def _rehearsal(tmp_path: Path, api: FakeApi, credentials: PlaidCredentials = SANDBOX) -> Any:
    return SandboxRehearsal(
        credentials,
        token_store=TokenStore(tmp_path / "tokens"),
        database=paths_for(credentials.environment, data_dir=tmp_path).database,
        api=api,
    )


def test_the_rehearsal_refuses_production_credentials(tmp_path: Path) -> None:
    """The task's one 'must not'. F2a makes a mistake here permanent."""
    with pytest.raises(RehearsalError, match="refuses to run against 'production'"):
        _rehearsal(tmp_path, FakeApi(), PRODUCTION)


def test_refusing_production_happens_before_any_call_is_made(tmp_path: Path) -> None:
    """Refusing after the first request would already have reached Plaid."""
    api = FakeApi()
    with pytest.raises(RehearsalError):
        _rehearsal(tmp_path, api, PRODUCTION)
    assert api.requests == []


def test_a_full_cycle_links_exchanges_and_fetches(tmp_path: Path) -> None:
    outcome = _rehearsal(tmp_path, FakeApi()).run()

    assert outcome.environment is PlaidEnvironment.SANDBOX
    assert outcome.institution_id == INSTITUTION
    assert outcome.item_id == "item-synthetic"
    assert (outcome.account_count, outcome.holding_count, outcome.security_count) == (1, 1, 1)


def test_the_cycle_runs_the_four_calls_in_the_order_production_will(tmp_path: Path) -> None:
    api = FakeApi()
    _rehearsal(tmp_path, api).run()

    assert [name for name, _ in api.requests] == [
        "institutions_get",
        "sandbox_public_token_create",
        "item_public_token_exchange",
        "accounts_balance_get",
        "investments_holdings_get",
    ]


def test_the_sandbox_link_uses_user_good_and_pass_good(tmp_path: Path) -> None:
    """The acceptance criterion names the test user; assert it rather than assume it."""
    api = FakeApi()
    _rehearsal(tmp_path, api).run()

    request = dict(api.requests)["sandbox_public_token_create"]
    assert request.options.override_username == SANDBOX_USERNAME == "user_good"
    assert request.options.override_password == SANDBOX_PASSWORD == "pass_good"


def test_no_institution_is_named_by_this_program(tmp_path: Path) -> None:
    """AGENTS.md rule 0: the institution is discovered, never asserted."""
    api = FakeApi()
    _rehearsal(tmp_path, api).run()

    asked = dict(api.requests)["institutions_get"]
    assert [str(product.value) for product in asked.products] == ["investments"]
    used = dict(api.requests)["sandbox_public_token_create"]
    assert used.institution_id == INSTITUTION  # from the response, not from a constant


def test_the_access_token_is_durable_before_the_outcome_is_returned(tmp_path: Path) -> None:
    """§14a's ordering: material lands durably, then the caller records an item."""
    store = TokenStore(tmp_path / "tokens")
    rehearsal = SandboxRehearsal(
        SANDBOX,
        token_store=store,
        database=paths_for(PlaidEnvironment.SANDBOX, data_dir=tmp_path).database,
        api=FakeApi(),
    )

    outcome = rehearsal.run()

    kind, _ = parse_secret_ref(outcome.secret_ref)
    assert kind is SecretKind.ACCESS_TOKEN
    assert store.get(outcome.secret_ref).reveal() == "access-sandbox-synthetic"
    assert store.record(outcome.secret_ref).item_id == "item-synthetic"


def test_the_rehearsal_writes_only_into_the_sandbox_database(tmp_path: Path) -> None:
    """Criterion 3, from this side: the database travels with the environment."""
    outcome = _rehearsal(tmp_path, FakeApi()).run()

    assert outcome.database == paths_for(PlaidEnvironment.SANDBOX, data_dir=tmp_path).database
    assert outcome.database.name == "networth-sandbox.db"
    assert outcome.database != paths_for(PlaidEnvironment.PRODUCTION, data_dir=tmp_path).database


def test_an_absent_field_and_a_null_field_are_different_observations() -> None:
    """§8.1 turns on this difference, so the observer must not collapse it.

    Absent means Plaid does not supply the field at all — a design constraint.
    Null means Plaid supplies it and has no value — an UNKNOWN age. A report that
    said 'no clock' for both would answer the empirical question wrongly in the
    direction that looks fine.
    """
    records = [SimpleNamespace(present_and_null=None)]

    observed = {o.path: o for o in _observe(records, ("present_and_null", "not_there"))}

    assert observed["present_and_null"] == FieldObservation("present_and_null", True, None)
    assert observed["present_and_null"].note == "null"
    assert observed["not_there"] == FieldObservation("not_there", False, None)
    assert observed["not_there"].note == "absent"


def test_a_field_type_is_taken_from_the_first_record_that_supplied_one() -> None:
    records = [SimpleNamespace(price=None), SimpleNamespace(price=1.5)]

    (observed,) = _observe(records, ("price",))

    assert observed.present is True
    assert observed.type_name == "float"


def test_observations_record_the_type_that_decides_whether_money_is_a_float(
    tmp_path: Path,
) -> None:
    """§7 forbids float money, so the type is the finding — never the amount."""
    outcome = _rehearsal(tmp_path, FakeApi()).run()

    balances = {o.path: o for o in outcome.accounts}
    assert balances["balances.current"].type_name == "float"
    assert balances["balances.available"].note == "null"


def test_a_plaid_error_never_carries_the_response_body_into_the_message(
    tmp_path: Path,
) -> None:
    """The same exception type is raised by the calls that carry the credential."""
    exc = ApiException(status=400, reason="Bad Request")
    exc.body = '{"error_code":"INVALID_FIELD","secret":"never-print-me"}'
    api = FakeApi(institutions_get=exc)

    with pytest.raises(RehearsalError) as raised:
        _rehearsal(tmp_path, api).run()

    message = str(raised.value)
    assert "institutions/get failed" in message
    assert "HTTP 400" in message
    assert "never-print-me" not in message
    assert "INVALID_FIELD" not in message


def test_a_transport_failure_is_reported_without_inventing_a_result(tmp_path: Path) -> None:
    api = FakeApi(accounts_balance_get=OSError("connection reset"))

    with pytest.raises(RehearsalError, match="accounts/balance/get failed: OSError"):
        _rehearsal(tmp_path, api).run()


def test_an_institution_list_with_no_match_stops_rather_than_guessing(tmp_path: Path) -> None:
    api = FakeApi(institutions_get=SimpleNamespace(institutions=[]))

    with pytest.raises(RehearsalError, match="no institution supporting investments"):
        _rehearsal(tmp_path, api).run()


def test_an_exchange_missing_its_item_id_is_a_failure_not_a_half_recorded_item(
    tmp_path: Path,
) -> None:
    """A stored token whose item_id is unknown is the orphan §14a works to avoid."""
    api = FakeApi(item_public_token_exchange=SimpleNamespace(access_token="access-x", item_id=None))
    store = TokenStore(tmp_path / "tokens")
    rehearsal = SandboxRehearsal(
        SANDBOX,
        token_store=store,
        database=paths_for(PlaidEnvironment.SANDBOX, data_dir=tmp_path).database,
        api=api,
    )

    with pytest.raises(RehearsalError, match="no access_token or no item_id"):
        rehearsal.run()

    assert list(store.directory.glob("*")) == []
