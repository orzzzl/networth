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
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import plaid
import urllib3.exceptions
from plaid.api import plaid_api
from plaid.exceptions import ApiException
from plaid.model.item_get_request import ItemGetRequest

from networth.plaid.environment import PlaidCredentials, PlaidEnvironment
from networth.plaid.errors import (
    HEALTHY,
    Classification,
    ItemState,
    classify_error,
    malformed_response,
    transport_failure,
)

# The SDK's transport is urllib3 and it passes almost all of it straight
# through: `plaid/rest.py` catches exactly one urllib3 exception (`SSLError`,
# which it turns into `ApiException(status=0)`) and lets the rest escape. So a
# connection reset, a DNS failure or exhausted retries arrives here as a
# `urllib3.exceptions.HTTPError` — which, despite the name, is not an `OSError`
# and is not an `ApiException`. Catching `OSError` alone therefore missed the
# ordinary case of the host being unreachable (found in review, round 1).
#
# The base class is deliberate: it is the whole urllib3 family
# (`MaxRetryError`, `ProtocolError`, `ReadTimeoutError`, `NewConnectionError`,
# …) and nothing else. `Exception` would also swallow the SDK's `ApiTypeError`
# and `ApiValueError`, which mean *we* built a bad request — a bug that must
# crash loudly rather than be reported as "the institution is having trouble".
# `LocationValueError` is inside this family and is that same kind of bug; it
# is re-raised explicitly below.
_TRANSPORT_ERRORS = (urllib3.exceptions.HTTPError, OSError)

# Distinguishes "the attribute is absent" from "the attribute is None". For
# `item.error` those are opposite facts: absent means the response was not the
# shape we understand, None means Plaid affirmatively reported no error.
_MISSING = object()


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
    investments_status_observed: bool = False
    investments_last_successful_update: datetime | None = None


def _error_fields(error: Any) -> tuple[str | None, str | None]:
    """``(error_code, error_type)`` out of an SDK error object that is present.

    Only ever called with a non-``None`` error, because the caller has already
    decided that an error exists — this function's answer is provenance for a
    classification, never the evidence for one. Either field coming back
    ``None`` means the error object did not carry it, which is a fact about the
    payload and not a reason to call the Item healthy.

    The SDK renders enums as objects with a ``value``; ``str()`` on the wrong
    one would produce something that matches no code in the taxonomy and would
    therefore be classified as unrecognised — a silent downgrade of a code we do
    know. Take ``.value`` when it is there.
    """

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


def _investments_update(response: Any) -> tuple[bool, datetime | None, str | None]:
    """Read ``status.investments.last_successful_update`` without inventing it.

    The entire status block is optional for Items without Investments.  A
    missing block therefore means "not observed", while a literal null clock
    means Plaid supplied the field but has no successful update to report.  The
    distinction lets the poller preserve an older observation across a
    transport failure while still recording UNKNOWN when Plaid says so.

    The SDK contract types a populated value as ``datetime``.  A different or
    naive value is an unreadable response, never a timestamp coerced from
    ``str()``; the returned issue contains no response data and is safe to
    persist.
    """

    status = getattr(response, "status", _MISSING)
    if status is _MISSING or status is None:
        return False, None, None
    investments = getattr(status, "investments", _MISSING)
    if investments is _MISSING or investments is None:
        return False, None, None
    value = getattr(investments, "last_successful_update", _MISSING)
    if value is _MISSING:
        return False, None, None
    if value is None:
        return True, None, None
    if not isinstance(value, datetime) or value.utcoffset() is None:
        return False, None, "investments last_successful_update was not an aware datetime"
    return True, value.astimezone(UTC), None


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
            if code is None and kind is None:
                return ItemStatus(
                    item_id=None,
                    classification=transport_failure(f"HTTP {exc.status} with no Plaid error body"),
                    request_id=None,
                )
            return ItemStatus(
                item_id=None,
                classification=classify_error(code, kind),
                request_id=None,
            )
        except urllib3.exceptions.LocationValueError:
            # The one member of the urllib3 family that is a programmer error
            # rather than an environmental one — it is also a `ValueError`, and
            # it means the URL *we* built is not a URL. Reported as DEGRADED it
            # would mark the Item unhealthy forever with a cause nobody can see;
            # the host is a constant per environment, so this can only ever be
            # our bug. Re-raised before the broad clause below, which would
            # otherwise catch it by inheritance.
            raise
        except _TRANSPORT_ERRORS as exc:
            return ItemStatus(
                item_id=None,
                classification=transport_failure(type(exc).__name__),
                request_id=None,
            )

        request_id = cast("str | None", getattr(response, "request_id", None))
        item = getattr(response, "item", _MISSING)
        if item is _MISSING or item is None:
            return ItemStatus(
                item_id=None,
                classification=malformed_response("the response carried no item"),
                request_id=request_id,
            )

        item_id = cast("str | None", getattr(item, "item_id", None))
        error = getattr(item, "error", _MISSING)
        if error is _MISSING:
            return ItemStatus(
                item_id=item_id,
                classification=malformed_response("the item carried no error field"),
                request_id=request_id,
            )

        # The only path to HEALTHY in this program: Plaid answered, the answer
        # had an Item, and that Item's error field is present and null.
        if error is None:
            classification = HEALTHY
        else:
            code, kind = _error_fields(error)
            classification = classify_error(code, kind)

        investments_observed, investments_update, investments_issue = _investments_update(response)
        if investments_issue is not None and classification.state in (
            ItemState.HEALTHY,
            None,
        ):
            classification = malformed_response(investments_issue)
            investments_observed = False
            investments_update = None

        return ItemStatus(
            item_id=item_id,
            classification=classification,
            request_id=request_id,
            investments_status_observed=investments_observed,
            investments_last_successful_update=investments_update,
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
