"""No live calls (task 05). Every branch is driven by a synthetic API object, so
what is under test is the wrapper's promise: a caller never has to catch a Plaid
exception to learn that an Item needs re-authentication."""

from __future__ import annotations

import json
from typing import Any

import pytest
import urllib3.exceptions
from plaid.exceptions import ApiException, ApiTypeError, ApiValueError

from networth.plaid.client import PlaidClient
from networth.plaid.environment import PlaidCredentials, PlaidEnvironment
from networth.plaid.errors import ItemState

CREDENTIALS = PlaidCredentials(
    client_id="synthetic-client",
    secret="synthetic-secret",
    environment=PlaidEnvironment.SANDBOX,
)


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
    def __init__(self, item: Any, request_id: str = "synthetic-request") -> None:
        self.item = item
        self.request_id = request_id


class ResponseWithNoItem:
    def __init__(self, request_id: str = "synthetic-request") -> None:
        self.request_id = request_id


class FakeApi:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[str] = []

    def item_get(self, item_get_request: Any) -> Any:
        self.calls.append(item_get_request.access_token)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


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
