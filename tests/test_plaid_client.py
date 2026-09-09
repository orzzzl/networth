"""No live calls (task 05). Every branch is driven by a synthetic API object, so
what is under test is the wrapper's promise: a caller never has to catch a Plaid
exception to learn that an Item needs re-authentication."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
import urllib3.exceptions
from plaid.exceptions import ApiException, ApiTypeError, ApiValueError
from plaid.model_utils import model_to_dict

from networth.plaid.client import ExchangedItem, PlaidCallError, PlaidClient
from networth.plaid.environment import PlaidCredentials, PlaidEnvironment
from networth.plaid.errors import ItemState
from tests.fake_plaid import (
    ACCESS_TOKEN,
    EXCHANGE_REQUEST_ID,
    INSTITUTION,
    ITEM_ID,
    FakeSandboxApi,
)

CREDENTIALS = PlaidCredentials(
    client_id="synthetic-client",
    secret="synthetic-secret",
    environment=PlaidEnvironment.SANDBOX,
)

_MISSING = object()


class FakeError:
    def __init__(self, code: str, kind: str) -> None:
        self.error_code = code
        self.error_type = kind


class FakeEnum:
    """The SDK renders enum fields as objects carrying ``.value``; ``str()`` on
    one of these produces something that matches no code in the taxonomy."""

    def __init__(self, value: str) -> None:
        self.value = value


class FakeItem:
    def __init__(self, item_id: str, error: Any = None) -> None:
        self.item_id = item_id
        self.error = error


class ItemWithNoErrorField:
    """An Item object that never had an ``error`` attribute at all.

    Distinct from ``FakeItem(error=None)``, which is Plaid affirmatively saying
    there is no error. This one is a response shape we do not understand, and
    the difference between the two is the difference between health and an
    anomaly."""

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id


class FakeResponse:
    def __init__(
        self,
        item: Any,
        request_id: str = "synthetic-request",
        *,
        status: Any = _MISSING,
    ) -> None:
        self.item = item
        self.request_id = request_id
        if status is not _MISSING:
            self.status = status


class FakeInvestmentsStatus:
    def __init__(self, last_successful_update: Any) -> None:
        self.last_successful_update = last_successful_update


class FakeStatus:
    def __init__(self, last_successful_update: Any) -> None:
        self.investments = FakeInvestmentsStatus(last_successful_update)


class ResponseWithNoItem:
    def __init__(self, request_id: str = "synthetic-request") -> None:
        self.request_id = request_id


class FakeApi:
    """The item-status fake. Task 05's tests need ``item_get`` and nothing else.

    The other seven methods exist because the SDK protocol has eight and this object
    stands in for the SDK; each one raises, so a test that reaches an endpoint it
    did not mean to fails instead of being quietly answered. (The Link tests use
    ``tests.fake_plaid.FakeSandboxApi``, which is the mirror image of this.)
    """

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[str] = []

    def item_get(self, item_get_request: Any) -> Any:
        self.calls.append(item_get_request.access_token)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def institutions_get(self, institutions_get_request: Any) -> Any:
        raise AssertionError("an item-status test must not call /institutions/get")

    def sandbox_public_token_create(self, sandbox_public_token_create_request: Any) -> Any:
        raise AssertionError("an item-status test must not call /sandbox/public_token/create")

    def item_public_token_exchange(self, item_public_token_exchange_request: Any) -> Any:
        raise AssertionError("an item-status test must not call /item/public_token/exchange")

    def link_token_create(self, link_token_create_request: Any) -> Any:
        raise AssertionError("an item-status test must not call /link/token/create")

    def link_token_get(self, link_token_get_request: Any) -> Any:
        raise AssertionError("an item-status test must not call /link/token/get")

    def accounts_balance_get(self, accounts_balance_get_request: Any) -> Any:
        raise AssertionError("an item-status test must not call /accounts/balance/get")

    def accounts_get(self, accounts_get_request: Any) -> Any:
        raise AssertionError("an item-status test must not call /accounts/get")

    def investments_holdings_get(self, investments_holdings_get_request: Any) -> Any:
        raise AssertionError("an item-status test must not call /investments/holdings/get")


def client_for(outcome: Any) -> tuple[PlaidClient, FakeApi]:
    api = FakeApi(outcome)
    return PlaidClient(CREDENTIALS, api=api), api


def api_exception(status: int, body: str | None) -> ApiException:
    exc = ApiException(status=status)
    exc.body = body
    return exc


def test_clean_response_is_healthy() -> None:
    client, api = client_for(FakeResponse(FakeItem("synthetic-item-1")))
    status = client.item_get("synthetic-access-token")
    assert status.classification.state is ItemState.HEALTHY
    assert status.item_id == "synthetic-item-1"
    assert status.request_id == "synthetic-request"
    assert api.calls == ["synthetic-access-token"]


def test_investments_source_clock_is_extracted_and_normalised_to_utc() -> None:
    reported = datetime(2026, 1, 15, 4, 30, tzinfo=timezone(timedelta(hours=-8)))
    client, _ = client_for(FakeResponse(FakeItem("synthetic-item-1"), status=FakeStatus(reported)))

    status = client.item_get("synthetic-access-token")

    assert status.classification.state is ItemState.HEALTHY
    assert status.investments_status_observed
    assert status.investments_last_successful_update == datetime(2026, 1, 15, 12, 30, tzinfo=UTC)


def test_literal_null_investments_clock_is_observed_unknown() -> None:
    client, _ = client_for(FakeResponse(FakeItem("synthetic-item-1"), status=FakeStatus(None)))

    status = client.item_get("synthetic-access-token")

    assert status.investments_status_observed
    assert status.investments_last_successful_update is None


def test_absent_investments_status_is_not_an_observed_null() -> None:
    client, _ = client_for(FakeResponse(FakeItem("synthetic-item-1")))

    status = client.item_get("synthetic-access-token")

    assert not status.investments_status_observed
    assert status.investments_last_successful_update is None


def test_unreadable_investments_clock_cannot_leave_the_item_healthy() -> None:
    client, _ = client_for(
        FakeResponse(
            FakeItem("synthetic-item-1"),
            status=FakeStatus(datetime(2026, 1, 15, 12, 30)),
        )
    )

    status = client.item_get("synthetic-access-token")

    assert status.classification.state is ItemState.DEGRADED
    assert not status.classification.recognised
    assert not status.investments_status_observed


def test_error_in_the_payload_is_classified() -> None:
    """A 200 whose Item carries an error is how `/item/get` reports most of Axis
    A — the call succeeded and the connection is still dead."""
    client, _ = client_for(
        FakeResponse(FakeItem("synthetic-item-1", FakeError("ITEM_LOGIN_REQUIRED", "ITEM_ERROR")))
    )
    status = client.item_get("synthetic-access-token")
    assert status.classification.state is ItemState.NEEDS_REAUTH
    assert status.classification.recognised
    assert status.classification.owner_actionable


def test_enum_shaped_error_fields_are_unwrapped() -> None:
    """Without ``.value`` this would classify as an unrecognised code — a known
    error silently downgraded to "we have never seen this"."""
    client, _ = client_for(
        FakeResponse(
            FakeItem(
                "synthetic-item-1",
                FakeError(FakeEnum("ITEM_LOGIN_REQUIRED"), FakeEnum("ITEM_ERROR")),  # type: ignore[arg-type]
            )
        )
    )
    status = client.item_get("synthetic-access-token")
    assert status.classification.error_code == "ITEM_LOGIN_REQUIRED"
    assert status.classification.state is ItemState.NEEDS_REAUTH
    assert status.classification.recognised


def test_http_error_body_is_classified_not_raised() -> None:
    body = json.dumps(
        {"error_code": "USER_PERMISSION_REVOKED", "error_type": "ITEM_ERROR", "request_id": "r"}
    )
    client, _ = client_for(api_exception(400, body))
    status = client.item_get("synthetic-access-token")
    assert status.classification.state is ItemState.REVOKED
    assert status.classification.recognised


def test_unparseable_error_body_becomes_a_transport_failure() -> None:
    """The failure path must not itself fail: a body that is not the JSON we
    expect would otherwise replace a classified error with a crash in the
    poller."""
    client, _ = client_for(api_exception(502, "<html>gateway</html>"))
    status = client.item_get("synthetic-access-token")
    assert status.classification.state is ItemState.DEGRADED
    assert status.classification.recognised
    assert "502" in status.classification.detail


def test_body_that_is_json_but_not_an_object_is_survived() -> None:
    client, _ = client_for(api_exception(500, "[]"))
    assert client.item_get("t").classification.state is ItemState.DEGRADED


def test_missing_body_is_survived() -> None:
    client, _ = client_for(api_exception(500, None))
    assert client.item_get("t").classification.state is ItemState.DEGRADED


def test_bytes_body_is_decoded() -> None:
    exc = api_exception(400, None)
    exc.body = json.dumps({"error_code": "ITEM_NOT_FOUND", "error_type": "ITEM_ERROR"}).encode()
    client, _ = client_for(exc)
    assert client.item_get("t").classification.state is ItemState.REVOKED


def test_transport_failure_is_not_an_exception_for_the_caller() -> None:
    client, _ = client_for(ConnectionResetError("reset by peer"))
    status = client.item_get("synthetic-access-token")
    assert status.classification.state is ItemState.DEGRADED
    assert status.item_id is None


def test_an_error_object_with_a_type_but_no_code_is_not_healthy() -> None:
    """Plaid's error object has several fields and nothing guarantees a code is
    among them. Reading "no code" as "no error" made a real ITEM_ERROR come out
    of this wrapper as a live connection (found in review, round 1)."""
    error = FakeError("", "ITEM_ERROR")
    error.error_code = None  # type: ignore[assignment]
    client, _ = client_for(FakeResponse(FakeItem("synthetic-item-1", error)))
    status = client.item_get("synthetic-access-token")
    assert status.classification.state is ItemState.DEGRADED
    assert not status.classification.recognised
    assert status.classification.error_type == "ITEM_ERROR"


def test_an_empty_error_object_is_not_healthy() -> None:
    """The weakest signal Plaid could send that is still a signal: an error
    object with neither field set. Its presence is the fact that matters."""
    error = FakeError("", "")
    error.error_code = None  # type: ignore[assignment]
    error.error_type = None  # type: ignore[assignment]
    client, _ = client_for(FakeResponse(FakeItem("synthetic-item-1", error)))
    status = client.item_get("synthetic-access-token")
    assert status.classification.state is ItemState.DEGRADED
    assert not status.classification.recognised


def test_a_response_with_no_item_is_not_healthy() -> None:
    """There is nothing to be healthy *about*. This came back HEALTHY, with a
    `None` item_id, because the absent Item produced an absent error code."""
    client, _ = client_for(ResponseWithNoItem())
    status = client.item_get("synthetic-access-token")
    assert status.classification.state is ItemState.DEGRADED
    assert not status.classification.recognised
    assert status.item_id is None
    assert status.request_id == "synthetic-request"


def test_a_null_item_is_not_healthy() -> None:
    client, _ = client_for(FakeResponse(None))
    assert client.item_get("t").classification.state is ItemState.DEGRADED


def test_an_item_with_no_error_field_is_not_healthy() -> None:
    """`error=None` means Plaid checked and found nothing wrong; no `error`
    field at all means the response is not the shape we parse. Only the first
    is evidence of health, and the Item id is still reported so the anomaly can
    be traced to an Item."""
    client, _ = client_for(FakeResponse(ItemWithNoErrorField("synthetic-item-1")))
    status = client.item_get("synthetic-access-token")
    assert status.classification.state is ItemState.DEGRADED
    assert not status.classification.recognised
    assert status.item_id == "synthetic-item-1"


def test_retry_exhaustion_is_degraded_not_an_exception() -> None:
    """`MaxRetryError` is what the SDK's transport actually raises when the host
    is unreachable — `plaid/rest.py` catches only urllib3's `SSLError` and lets
    the rest through. It is not an `OSError` and not an `ApiException`, so the
    original two-clause `except` let it escape into the poller (found in review,
    round 1)."""
    exc = urllib3.exceptions.MaxRetryError(
        pool=None,  # type: ignore[arg-type]
        url="/item/get",
        reason=urllib3.exceptions.NewConnectionError(None, "connection refused"),  # type: ignore[arg-type]
    )
    client, _ = client_for(exc)
    status = client.item_get("synthetic-access-token")
    assert status.classification.state is ItemState.DEGRADED
    assert status.classification.recognised
    assert status.item_id is None


def test_protocol_error_is_degraded_not_an_exception() -> None:
    """The other everyday one: the connection dies mid-response."""
    client, _ = client_for(urllib3.exceptions.ProtocolError("connection aborted"))
    assert client.item_get("t").classification.state is ItemState.DEGRADED


def test_read_timeout_is_degraded_not_an_exception() -> None:
    client, _ = client_for(urllib3.exceptions.ReadTimeoutError(None, "/item/get", "timed out"))  # type: ignore[arg-type]
    assert client.item_get("t").classification.state is ItemState.DEGRADED


@pytest.mark.parametrize(
    "exc",
    [
        ApiTypeError("wrong type for access_token"),
        ApiValueError("both body and post_params given"),
        TypeError("item_get() got an unexpected keyword argument"),
        AttributeError("no attribute 'item_get'"),
        # Inside urllib3's own exception tree, and the reason the transport
        # clause cannot simply be `urllib3.exceptions.HTTPError`: this one is
        # also a `ValueError` and means the URL we built is not a URL. The
        # fix for the escaping-transport-error blocker introduced this hole
        # and closes it explicitly.
        urllib3.exceptions.LocationParseError("not-a-url"),
        urllib3.exceptions.URLSchemeUnknown("gopher"),
    ],
)
def test_our_own_bugs_are_not_reported_as_the_bank_being_down(exc: Exception) -> None:
    """The counterweight to the fix above. Widening the `except` to `Exception`
    would also catch a malformed request we built, and this program would then
    report a bug of ours as DEGRADED — an Item marked unhealthy forever with a
    cause nobody can see. These must reach the caller."""
    client, _ = client_for(exc)
    with pytest.raises(type(exc)):
        client.item_get("t")


def test_no_outcome_of_item_get_is_healthy_by_accident() -> None:
    """The wrapper's whole reason to exist: every shape of failure it can meet,
    none of which may look like a working connection."""
    codeless = FakeError("", "ITEM_ERROR")
    codeless.error_code = None  # type: ignore[assignment]
    outcomes: list[Any] = [
        api_exception(400, json.dumps({"error_code": "ITEM_LOGIN_REQUIRED"})),
        api_exception(400, json.dumps({"error_type": "ITEM_ERROR"})),
        api_exception(502, "<html>gateway</html>"),
        ConnectionResetError("reset"),
        urllib3.exceptions.ProtocolError("connection aborted"),
        urllib3.exceptions.ReadTimeoutError(None, "/item/get", "timed out"),  # type: ignore[arg-type]
        FakeResponse(FakeItem("i", FakeError("SOMETHING_UNSEEN", "ITEM_ERROR"))),
        FakeResponse(FakeItem("i", codeless)),
        FakeResponse(ItemWithNoErrorField("i")),
        FakeResponse(None),
        ResponseWithNoItem(),
    ]
    for outcome in outcomes:
        client, _ = client_for(outcome)
        assert client.item_get("t").classification.state is not ItemState.HEALTHY, outcome


def test_the_access_token_is_not_in_the_result() -> None:
    """It is the credential this whole project is built around not losing and
    not leaking; nothing in the returned object should carry it."""
    token = "synthetic-access-token-value"
    client, _ = client_for(FakeResponse(FakeItem("synthetic-item-1")))
    status = client.item_get(token)
    assert token not in repr(status)


def test_environment_is_readable_from_the_client() -> None:
    client, _ = client_for(FakeResponse(FakeItem("i")))
    assert client.environment is PlaidEnvironment.SANDBOX


def test_real_api_is_built_against_the_selected_host() -> None:
    """Constructing the SDK client makes no network call, so this stays inside
    the no-live-calls rule while proving the credential and the host cannot be
    chosen separately (section 15)."""
    for environment in PlaidEnvironment:
        credentials = PlaidCredentials("synthetic-client", "synthetic-secret", environment)
        client = PlaidClient(credentials)
        configuration = client._api.api_client.configuration  # type: ignore[attr-defined]
        assert configuration.host == environment.api_host


# --- The Link, exchange and fetch calls (task 06) ------------------------------
#
# They live on this seam because DESIGN.md section 5 says the SDK stops here, and
# because the redaction, the environment pairing and the conversion into our own
# types are all properties of the seam rather than of any one caller.

PRODUCTION_CREDENTIALS = PlaidCredentials(
    client_id="synthetic-client",
    secret="synthetic-secret",
    environment=PlaidEnvironment.PRODUCTION,
)

_HOLDING_FIELDS = ("security_id", "institution_price_as_of")
_SECURITY_FIELDS = ("security_id", "close_price_as_of")


def sandbox_client(**overrides: Any) -> tuple[PlaidClient, FakeSandboxApi]:
    api = FakeSandboxApi(**overrides)
    return PlaidClient(CREDENTIALS, api=api), api


def test_the_sandbox_only_endpoint_refuses_production_before_building_a_request() -> None:
    """F2: the master credential must not even be *attached* to this attempt."""
    api = FakeSandboxApi()
    client = PlaidClient(PRODUCTION_CREDENTIALS, api=api)

    with pytest.raises(PlaidCallError, match="refuses to run against 'production'"):
        client.sandbox_public_token_create(
            institution_id="ins_x", products=("investments",), username="u", password="p"
        )

    assert api.requests == []


def test_an_institution_is_discovered_with_the_products_the_caller_asked_for() -> None:
    client, api = sandbox_client()

    institution_id = client.first_institution_supporting(
        products=("investments",), country_codes=("US",)
    )

    assert institution_id == INSTITUTION
    asked = api.request_for("institutions_get")
    assert [str(p.value) for p in asked.options.products] == ["investments"]
    assert [str(c.value) for c in asked.country_codes] == ["US"]


def test_the_institutions_request_puts_products_where_plaid_defines_it() -> None:
    """The regression test for the first live Sandbox failure (task 06, HTTP 400).

    The previous version of the test above asserted ``request.products`` — the
    same undeclared attribute the code had just set. ``InstitutionsGetRequest``
    permits arbitrary attributes (``additional_properties_type``), so setting and
    reading one back always agrees with itself: the assertion could not fail, and
    it certified a request Plaid rejects.

    So this asserts the **serialised** request instead, which is the only form
    Plaid is given, and it asserts the negative — ``products`` is not a top-level
    key — because that, not the presence of ``options``, is what produced the 400.
    """
    client, api = sandbox_client()

    client.first_institution_supporting(products=("investments",), country_codes=("US",))

    wire = model_to_dict(api.request_for("institutions_get"), serialize=True)

    assert wire["options"]["products"] == ["investments"]
    assert "products" not in wire, (
        "products at the top level is what /institutions/get answers HTTP 400 to; "
        f"the serialised request was {wire}"
    )


def test_an_empty_institution_list_is_a_failure_not_an_empty_string() -> None:
    client, _ = sandbox_client(institutions_get=SimpleNamespace(institutions=[]))

    with pytest.raises(PlaidCallError, match="no institution supporting"):
        client.first_institution_supporting(products=("investments",), country_codes=("US",))


def test_an_institution_without_an_id_is_a_failure() -> None:
    client, _ = sandbox_client(
        institutions_get=SimpleNamespace(institutions=[SimpleNamespace(institution_id=None)])
    )

    with pytest.raises(PlaidCallError, match="no institution_id"):
        client.first_institution_supporting(products=("investments",), country_codes=("US",))


def raising_sandbox_client(exc: Exception) -> PlaidClient:
    def boom(_request: Any) -> Any:
        raise exc

    return PlaidClient(CREDENTIALS, api=SimpleNamespace(institutions_get=boom))


def test_an_http_error_names_the_request_id_it_tells_the_reader_to_look_up() -> None:
    """The message has always said "read it from the dashboard by request id" and
    never printed one, so the first live HTTP 400 was undiagnosable (task 06)."""
    client = raising_sandbox_client(
        api_exception(400, json.dumps({"request_id": "abc123XYZ", "error_code": "INVALID_FIELD"}))
    )

    with pytest.raises(PlaidCallError) as caught:
        client.first_institution_supporting(products=("investments",), country_codes=("US",))

    assert "abc123XYZ" in str(caught.value)
    assert "HTTP 400" in str(caught.value)


def test_a_body_with_no_usable_request_id_says_so_instead_of_inventing_one() -> None:
    for body in (None, "", "not json at all", "[]", json.dumps({"error_code": "INVALID_FIELD"})):
        client = raising_sandbox_client(api_exception(400, body))

        with pytest.raises(PlaidCallError) as caught:
            client.first_institution_supporting(products=("investments",), country_codes=("US",))

        assert "carried no request id" in str(caught.value), body


def test_nothing_but_a_plain_token_escapes_the_body_the_error_refuses_to_show() -> None:
    """The request id is the single field lifted out of a redacted body, so it is
    validated rather than trusted: a body is attacker-shaped in the general case,
    and this string is printed, logged and pasted into PRs."""
    smuggled = (
        "secret sandbox-abc123 leaked",
        "x" * 65,
        "has\nnewline",
        {"nested": "object"},
        1234,
        None,
    )
    for reference in smuggled:
        client = raising_sandbox_client(api_exception(400, json.dumps({"request_id": reference})))

        with pytest.raises(PlaidCallError) as caught:
            client.first_institution_supporting(products=("investments",), country_codes=("US",))

        message = str(caught.value)
        assert "carried no request id" in message, reference
        assert "leaked" not in message
        assert "nested" not in message


def test_a_public_token_that_did_not_come_back_is_a_failure() -> None:
    client, _ = sandbox_client(
        sandbox_public_token_create=SimpleNamespace(public_token=None),
    )

    with pytest.raises(PlaidCallError, match="no public_token"):
        client.sandbox_public_token_create(
            institution_id="ins_x", products=("investments",), username="u", password="p"
        )


def test_the_exchange_returns_our_own_type_with_both_halves() -> None:
    client, _ = sandbox_client()

    item = client.item_public_token_exchange("public-token")

    assert (item.access_token, item.item_id) == (ACCESS_TOKEN, ITEM_ID)
    assert type(item).__module__.startswith("networth.")


def test_an_exchange_missing_either_half_is_refused() -> None:
    for response in (
        SimpleNamespace(access_token="a", item_id=None),
        SimpleNamespace(access_token=None, item_id="i"),
    ):
        client, _ = sandbox_client(item_public_token_exchange=response)
        with pytest.raises(PlaidCallError, match="no access_token or no item_id"):
            client.item_public_token_exchange("public-token")


def test_the_exchange_request_id_survives_the_wrapper() -> None:
    """The id reaches the caller, because `07a` records it and cannot invent it.

    Until PR #58 round 2 this wrapper read `access_token` and `item_id` and
    dropped the third required field on the floor, so every consumer downstream
    of the seam had to make one up. This is the regression for that.
    """
    client, _ = sandbox_client()

    item = client.item_public_token_exchange("public-token")

    assert item.request_id == EXCHANGE_REQUEST_ID


def test_the_locked_sdk_really_does_require_a_request_id_on_the_exchange() -> None:
    """Pin the SDK fact the marker's evidentiary value rests on.

    `link_exchange_attempt.request_id` is only usable as a success marker while
    a successful response is *guaranteed* to carry an id: a NULL there otherwise
    means both "never sent" and "sent, succeeded, no id", and the marker stops
    separating the crash boundaries it exists to separate.

    Nothing in this repository enforces that guarantee — the SDK does — which is
    precisely the `#36` shape: correct only because of a fact it never asserts.
    So it is asserted here, and an SDK bump that relaxes the field turns *this*
    test red instead of silently widening B3a.
    """
    from plaid.model.item_public_token_exchange_response import (
        ItemPublicTokenExchangeResponse,
    )

    # No `type: ignore` here, deliberately: the SDK ships no type information, so
    # this call is `Any` to mypy and a suppression would be dead. The guarantee is
    # a *runtime* one, which is the only form the marker can rely on anyway.
    with pytest.raises(TypeError, match="request_id"):
        ItemPublicTokenExchangeResponse(access_token="a", item_id="i")


def test_a_malformed_request_id_is_reported_absent_and_never_refused() -> None:
    """Validated exactly like the error path's, and not fatal — two rules at once.

    The grammar is `_REQUEST_ID`, the same one :func:`_request_id_of` applies to
    an error body, because this string is printed and pasted into PRs just like
    that one. But a bad id may not cost the credential: by **F2a** the lifetime
    slot was spent by the Link before this call, so raising here would strand a
    real Item over a support reference — rev 18's mistake, one field over.
    """
    for hostile in ("not a valid id!", "x" * 65, "", None, 12345):
        client, _ = sandbox_client(
            item_public_token_exchange=SimpleNamespace(
                access_token=ACCESS_TOKEN, item_id=ITEM_ID, request_id=hostile
            )
        )

        item = client.item_public_token_exchange("public-token")

        assert item.request_id is None, hostile
        assert item.access_token == ACCESS_TOKEN, hostile


def test_the_exchange_will_not_construct_without_an_explicit_request_id() -> None:
    """A structural guard, and structurally invisible to mutation testing.

    `request_id` has no default on purpose: every construction site must say
    what it knows, and `None` has to be written down rather than fallen into.
    Reverting that to `request_id: str | None = None` leaves every other test in
    this repository green — the signature is the only thing that changed — so
    the revert is what this test names.
    """
    with pytest.raises(TypeError, match="request_id"):
        ExchangedItem(access_token="a", item_id="i")  # type: ignore[call-arg]


def test_balances_cross_the_seam_as_observations_and_not_as_figures() -> None:
    client, _ = sandbox_client()

    accounts = client.accounts_balance_get("access", fields=("account_id", "balances.current"))

    assert accounts.count == 1
    assert {f.path: f.note for f in accounts.fields} == {
        "account_id": "str",
        "balances.current": "float",
    }
    assert "1000.0" not in repr(accounts)


def test_holdings_and_securities_are_observed_separately() -> None:
    """The source clock is split across the two lists (§8.1), so both are read."""
    client, _ = sandbox_client()

    observed = client.investments_holdings_get(
        "access", holding_fields=_HOLDING_FIELDS, security_fields=_SECURITY_FIELDS
    )

    assert observed.holdings.name == "holdings"
    assert observed.securities.name == "securities"
    assert {f.path: f.note for f in observed.holdings.fields}["institution_price_as_of"] == "null"
    assert {f.path: f.note for f in observed.securities.fields}["close_price_as_of"] == "null"


def test_a_response_with_no_lists_at_all_is_zero_records_not_a_crash() -> None:
    client, _ = sandbox_client(investments_holdings_get=SimpleNamespace())

    observed = client.investments_holdings_get(
        "access", holding_fields=_HOLDING_FIELDS, security_fields=_SECURITY_FIELDS
    )

    assert (observed.holdings.count, observed.securities.count) == (0, 0)


def test_every_link_call_redacts_the_plaid_error_body() -> None:
    """One assertion per call, because the redaction is per-call plumbing."""
    exc = api_exception(400, '{"error_code":"INVALID_FIELD","secret":"never-print-me"}')
    cases: list[tuple[str, Callable[[PlaidClient], object], dict[str, Any]]] = [
        (
            "institutions/get",
            lambda c: c.first_institution_supporting(
                products=("investments",), country_codes=("US",)
            ),
            {"institutions_get": exc},
        ),
        (
            "sandbox/public_token/create",
            lambda c: c.sandbox_public_token_create(
                institution_id="ins_x", products=("investments",), username="u", password="p"
            ),
            {"sandbox_public_token_create": exc},
        ),
        (
            "item/public_token/exchange",
            lambda c: c.item_public_token_exchange("public-token"),
            {"item_public_token_exchange": exc},
        ),
        (
            "accounts/balance/get",
            lambda c: c.accounts_balance_get("access", fields=("account_id",)),
            {"accounts_balance_get": exc},
        ),
        (
            "investments/holdings/get",
            lambda c: c.investments_holdings_get(
                "access", holding_fields=_HOLDING_FIELDS, security_fields=_SECURITY_FIELDS
            ),
            {"investments_holdings_get": exc},
        ),
    ]
    for step, call, overrides in cases:
        client, _ = sandbox_client(**overrides)
        with pytest.raises(PlaidCallError) as raised:
            call(client)
        message = str(raised.value)
        assert message.startswith(f"{step} failed: Plaid returned HTTP 400"), message
        assert "never-print-me" not in message
        assert "INVALID_FIELD" not in message


def test_a_url_we_built_wrong_still_crashes_rather_than_reporting_a_call_failure() -> None:
    """The one urllib3 member that is our bug — same carve-out `item_get` documents."""
    client, _ = sandbox_client(
        accounts_balance_get=urllib3.exceptions.LocationValueError("no host set")
    )

    with pytest.raises(urllib3.exceptions.LocationValueError):
        client.accounts_balance_get("access", fields=("account_id",))


def test_a_request_we_built_wrong_is_not_swallowed_as_a_call_failure() -> None:
    """`ApiTypeError`/`ApiValueError` mean we built a bad request; they must crash."""
    for exc in (ApiTypeError("bad type"), ApiValueError("bad value")):
        client, _ = sandbox_client(accounts_balance_get=exc)
        with pytest.raises((ApiTypeError, ApiValueError)):
            client.accounts_balance_get("access", fields=("account_id",))


def test_value_fetches_use_the_explicit_balance_endpoints_and_owned_types() -> None:
    client, api = sandbox_client()

    realtime = client.fetch_realtime_balances("access")
    cached = client.fetch_cached_balances("access")

    assert api.called[-2:] == ["accounts_balance_get", "accounts_get"]
    assert realtime == cached
    assert realtime[0].current == Decimal("1000.0")
    assert realtime[0].last_updated_datetime == datetime(2026, 9, 7, 14, 30, tzinfo=UTC)
    assert "acct-1" not in repr(realtime)
    assert "1000.0" not in repr(realtime)


def test_holdings_fetch_returns_account_totals_and_clock_evidence() -> None:
    client, api = sandbox_client()

    investments = client.fetch_holdings("access")

    assert api.called[-1] == "investments_holdings_get"
    assert investments.accounts[0].current == Decimal("1000.0")
    assert investments.holdings[0].institution_value == Decimal("1000.0")
    assert investments.holdings[0].institution_price_datetime == datetime(
        2026, 9, 5, 21, 0, tzinfo=UTC
    )
    assert "acct-1" not in repr(investments)
    assert "1000.0" not in repr(investments)


def test_value_fetch_refuses_unusable_provider_fields_without_echoing_them() -> None:
    malformed = SimpleNamespace(
        accounts=[
            SimpleNamespace(
                account_id="sensitive-account",
                balances=SimpleNamespace(
                    current=4321.99,
                    iso_currency_code="not-a-currency",
                    last_updated_datetime=None,
                ),
            )
        ]
    )
    client, _ = sandbox_client(accounts_balance_get=malformed)

    with pytest.raises(PlaidCallError) as raised:
        client.fetch_realtime_balances("access")

    assert str(raised.value) == "accounts/balance/get returned an unusable account record"
    assert "sensitive-account" not in str(raised.value)
    assert "4321.99" not in str(raised.value)
