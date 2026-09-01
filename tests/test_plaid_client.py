"""No live calls (task 05). Every branch is driven by a synthetic API object, so
what is under test is the wrapper's promise: a caller never has to catch a Plaid
exception to learn that an Item needs re-authentication."""

from __future__ import annotations

import json
from typing import Any

from plaid.exceptions import ApiException

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


class FakeResponse:
    def __init__(self, item: FakeItem, request_id: str = "synthetic-request") -> None:
        self.item = item
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


def test_no_outcome_of_item_get_is_healthy_by_accident() -> None:
    """The wrapper's whole reason to exist: four different shapes of failure,
    none of which may look like a working connection."""
    outcomes: list[Any] = [
        api_exception(400, json.dumps({"error_code": "ITEM_LOGIN_REQUIRED"})),
        api_exception(502, "<html>gateway</html>"),
        ConnectionResetError("reset"),
        FakeResponse(FakeItem("i", FakeError("SOMETHING_UNSEEN", "ITEM_ERROR"))),
    ]
    for outcome in outcomes:
        client, _ = client_for(outcome)
        assert client.item_get("t").classification.state is not ItemState.HEALTHY


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
