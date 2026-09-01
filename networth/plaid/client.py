"""The wrapper every Plaid call goes through.

Its whole job is that no caller ever sees a raw Plaid error. ``/item/get`` is
the only call here because it is the only one v0's mechanism needs: section 8.4
made polling the whole of Axis A after webhooks were dropped, and the Link calls
belong to the tasks that own the Link flow (06, 07a) rather than to this seam.

The official SDK is ``DESIGN.md`` section 16's choice, and task 05's entry names
section 16 as normative — hand-rolling an HTTP client for a financial API, on
the host that holds the credentials, is the thing that verdict rejected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, cast

import plaid
from plaid.api import plaid_api
from plaid.exceptions import ApiException
from plaid.model.item_get_request import ItemGetRequest

from networth.plaid.environment import PlaidCredentials, PlaidEnvironment
from networth.plaid.errors import Classification, classify, transport_failure


class _ItemGetApi(Protocol):
    """Only the part of the SDK this wrapper uses.

    Narrow on purpose: it is what lets the tests drive every branch below with
    synthetic responses instead of the network (task 05: no live calls in the
    test suite).
    """

    def item_get(self, item_get_request: ItemGetRequest) -> Any: ...


@dataclass(frozen=True, slots=True)
class ItemStatus:
    """What one ``/item/get`` established about one Item.

    ``item_id`` comes back from Plaid rather than from our database, so a reply
    about the wrong Item is visible to the caller instead of being assumed away.
    """

    item_id: str | None
    classification: Classification
    request_id: str | None


def _error_fields(error: Any) -> tuple[str | None, str | None]:
    """``(error_code, error_type)`` out of an SDK error object.

    The SDK renders enums as objects with a ``value``; ``str()`` on the wrong
    one would produce something that matches no code in the taxonomy and would
    therefore be classified as unrecognised — a silent downgrade of a code we do
    know. Take ``.value`` when it is there.
    """
    if error is None:
        return None, None

    def field(name: str) -> str | None:
        value = getattr(error, name, None)
        if value is None:
            return None
        value = getattr(value, "value", value)
        return str(value) if value != "" else None

    return field("error_code"), field("error_type")


def _api_exception_fields(exc: ApiException) -> tuple[str | None, str | None]:
    """Plaid puts the error in the response body of a 4xx/5xx.

    A body that is not the JSON we expect must not raise here: this function
    runs on the failure path, and a parser that throws would replace a
    classified error with a crash in the poller.
    """
    body = getattr(exc, "body", None)
    if body is None:
        return None, None
    if isinstance(body, bytes | bytearray):
        body = body.decode("utf-8", errors="replace")
    if not isinstance(body, str):
        return None, None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    code = parsed.get("error_code")
    kind = parsed.get("error_type")
    return (
        code if isinstance(code, str) and code else None,
        kind if isinstance(kind, str) and kind else None,
    )


class PlaidClient:
    """Plaid, with section 8.2's taxonomy applied to every answer."""

    def __init__(self, credentials: PlaidCredentials, *, api: _ItemGetApi | None = None) -> None:
        self._credentials = credentials
        self._api = api if api is not None else _build_api(credentials)

    @property
    def environment(self) -> PlaidEnvironment:
        return self._credentials.environment

    def item_get(self, access_token: str) -> ItemStatus:
        """Poll one Item. Never raises for a Plaid-level failure.

        Section 8.4's floor: this is what proves an Item's connection state in
        v0. Every outcome — clean, error in the payload, HTTP error, transport
        failure — leaves through :class:`ItemStatus`, because a caller that has
        to catch exceptions to learn about ``ITEM_LOGIN_REQUIRED`` is a caller
        that can forget to.
        """
        request = ItemGetRequest(access_token=access_token)
        try:
            response = self._api.item_get(request)
        except ApiException as exc:
            code, kind = _api_exception_fields(exc)
            if code is None:
                return ItemStatus(
                    item_id=None,
                    classification=transport_failure(f"HTTP {exc.status} with no Plaid error body"),
                    request_id=None,
                )
            return ItemStatus(
                item_id=None,
                classification=classify(code, kind),
                request_id=None,
            )
        except OSError as exc:
            return ItemStatus(
                item_id=None,
                classification=transport_failure(type(exc).__name__),
                request_id=None,
            )

        item = getattr(response, "item", None)
        code, kind = _error_fields(getattr(item, "error", None))
        return ItemStatus(
            item_id=cast("str | None", getattr(item, "item_id", None)),
            classification=classify(code, kind),
            request_id=cast("str | None", getattr(response, "request_id", None)),
        )


def _build_api(credentials: PlaidCredentials) -> _ItemGetApi:
    """The real SDK client, wired to the host the environment selected.

    The credential and the host come from the same object, so there is no
    arrangement of this code in which a Production secret is sent to Sandbox or
    the reverse (section 15).
    """
    configuration = plaid.Configuration(
        host=credentials.environment.api_host,
        api_key={
            "clientId": credentials.client_id,
            "secret": credentials.secret,
        },
    )
    return cast("_ItemGetApi", plaid_api.PlaidApi(plaid.ApiClient(configuration)))
