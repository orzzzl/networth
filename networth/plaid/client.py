"""The wrapper every Plaid call goes through.

Its whole job is that no caller ever sees a raw Plaid error — or a raw Plaid
object. ``DESIGN.md`` section 5 puts the SDK behind this seam and nowhere else,
so **every** endpoint this program calls has its method here, and each one
returns types this project owns.

Task ``05`` implemented only ``/item/get``, because section 8.4 made polling the
whole of Axis A once webhooks were dropped and that was the only call its
mechanism needed. That scoped the *work*, not the boundary: the Link, exchange
and fetch calls arrived with the tasks that own the Link flow (``06``, ``07a``)
and landed **here**, on this seam. *(This paragraph used to say those calls
"belong to" tasks 06 and 07a "rather than to this seam", which reads as
permission for a second module to hold an SDK client. Codex ruled against that
reading on 2026-09-07: section 5 is normative and there is one SDK-owning
module.)*

The official SDK is section 16's choice, and task 05's entry names section 16 as
normative — hand-rolling an HTTP client for a financial API, on the host that
holds the credentials, is the thing that verdict rejected.

**Two error contracts, on purpose.** :meth:`PlaidClient.item_get` never raises
for a Plaid-level failure: it feeds section 8.2's state machine, where "the
institution is having trouble" is a *state* and a caller that must catch an
exception to learn about ``ITEM_LOGIN_REQUIRED`` is a caller that can forget to.
The Link and fetch calls raise :class:`PlaidCallError`, because they are steps
in a procedure with nothing to record when a step does not happen. Both paths
redact identically: no response body, no headers, no credential.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, cast

import plaid
import urllib3.exceptions
from plaid.api import plaid_api
from plaid.exceptions import ApiException
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_request import InstitutionsGetRequest
from plaid.model.institutions_get_request_options import InstitutionsGetRequestOptions
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_hosted_link import LinkTokenCreateHostedLink
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_get_request import LinkTokenGetRequest
from plaid.model.products import Products
from plaid.model.sandbox_public_token_create_request import SandboxPublicTokenCreateRequest
from plaid.model.sandbox_public_token_create_request_options import (
    SandboxPublicTokenCreateRequestOptions,
)

from networth.plaid.environment import PlaidCredentials, PlaidEnvironment
from networth.plaid.errors import (
    HEALTHY,
    Classification,
    ItemState,
    classify_error,
    malformed_response,
    transport_failure,
)
from networth.plaid.observation import RecordSet, record_set

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


# A Plaid request id is the handle the dashboard indexes errors by, and
# `PlaidCallError` has always told the reader to use one — without ever printing
# it, which made the instruction unfollowable. The first live Sandbox run hit
# exactly that: an HTTP 400 with no way to look it up (task 06, 2026-09-08).
#
# **This is the one field lifted out of a body the class refuses to show, so it
# is validated rather than trusted.** The body is attacker-shaped in the general
# case and this string gets printed, logged and pasted into PRs. Anything that is
# not a short plain token is reported as absent, which keeps the redaction
# promise total: no free text from a Plaid body can reach a log through here.
_REQUEST_ID = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")


def _request_id_of(exc: ApiException) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, bytes | bytearray):
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(body, str):
        return None
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    reference = payload.get("request_id")
    if isinstance(reference, str) and _REQUEST_ID.match(reference):
        return reference
    return None


class PlaidCallError(RuntimeError):
    """A Plaid call did not produce what the caller needs, with no data in it.

    ``ApiException``'s string form carries the response body and the request
    headers. A Plaid error body is not itself a credential, but the same
    exception type is raised by the calls that send ``client_id`` and
    ``secret``, and this message gets printed, logged and pasted into PRs. The
    step and the exception type locate the failure; Plaid's own dashboard has
    the rest, by request id.
    """


class _PlaidApi(Protocol):
    """Only the SDK calls this wrapper makes.

    Narrow on purpose: it is what lets the tests drive every branch below with
    synthetic responses instead of the network (task 05's rule, which task 06
    inherits: the suite makes no live call).
    """

    def item_get(self, item_get_request: ItemGetRequest) -> Any: ...
    def institutions_get(self, institutions_get_request: InstitutionsGetRequest) -> Any: ...
    def sandbox_public_token_create(
        self, sandbox_public_token_create_request: SandboxPublicTokenCreateRequest
    ) -> Any: ...
    def item_public_token_exchange(
        self, item_public_token_exchange_request: ItemPublicTokenExchangeRequest
    ) -> Any: ...
    def link_token_create(self, link_token_create_request: LinkTokenCreateRequest) -> Any: ...
    def link_token_get(self, link_token_get_request: LinkTokenGetRequest) -> Any: ...
    def accounts_balance_get(
        self, accounts_balance_get_request: AccountsBalanceGetRequest
    ) -> Any: ...
    def investments_holdings_get(
        self, investments_holdings_get_request: InvestmentsHoldingsGetRequest
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class ExchangedItem:
    """What ``/item/public_token/exchange`` established, with the token redacted.

    The ``access_token`` is the long-lived credential for one Item (§15) and
    this object is the thing a traceback renders. The default dataclass repr
    would put it in any log line that touched an exception — the same defect
    :class:`~networth.plaid.environment.PlaidCredentials` documents, one call
    later. ``item_id`` is not a secret but is not rendered either: it names one
    of the owner's institutions, and ``AGENTS.md`` rule 0 keeps that out of
    anything this project writes down.

    ``request_id`` is the exception, and deliberately the same exception the
    error path already makes: it is **the one field lifted out of a Plaid body
    that this module shows**, because its entire purpose is to be quoted into a
    support ticket (issue #14, and `07b`'s fallback when a credential is lost).
    Redacting it would defeat the capture. It is therefore *validated rather
    than trusted*, against the same :data:`_REQUEST_ID` grammar
    :func:`_request_id_of` uses, so the redaction promise stays total no matter
    what the body contained.

    **It is optional here although the SDK declares it required, and that
    asymmetry with the two fields above is the point.** ``access_token`` and
    ``item_id`` are refused when missing because half a result is a stranded
    Item. ``request_id`` is not: by **F2a** the lifetime slot was already spent
    by the Link before this call, so discarding a *valid* credential over a
    missing or malformed support id would strand the very Item it was being
    careful about — rev 18's error committed one field over. A support id is a
    fallback; a credential is the thing.
    """

    access_token: str
    item_id: str
    request_id: str | None

    def __repr__(self) -> str:
        return (
            "ExchangedItem(access_token=<redacted>, item_id=<redacted>, "
            f"request_id={self.request_id!r})"
        )


@dataclass(frozen=True, slots=True)
class HostedLinkToken:
    """What ``/link/token/create`` established for one Hosted Link session.

    **Two clocks, kept apart on purpose** (issue #3, and the same discipline
    §8.1 applies to data). ``expires_at`` is Plaid's — it is the ``expiration``
    field of the response and it governs the *link token*. ``url_lifetime_seconds``
    is **ours**: the value this program asked for, which governs how long the
    hosted URL itself stays openable and is **not echoed back in the response**.
    They are different lengths and they run out independently, so a single
    "deadline" field would be a guess dressed as a measurement. Task ``06a`` (i)
    exists to measure the second one against the exchange window; until it has,
    neither may be presented to the owner as *the* deadline.

    ``url_lifetime_seconds`` is ``None`` when this program did not ask for one,
    which means Plaid's default applies and we do not know it. That is a
    different fact from any number and is stored as one.

    Both strings are redacted in the repr. The ``link_token`` is what
    ``/link/token/get`` is polled with, and **the hosted URL is openable by
    whoever holds it** — completing a Link through it spends one of the ten
    lifetime Item slots (**F2a**), which makes the URL a spendable thing and not
    a diagnostic to print into a log.
    """

    link_token: str
    hosted_link_url: str
    expires_at: datetime | None
    url_lifetime_seconds: int | None = None

    def __repr__(self) -> str:
        return (
            "HostedLinkToken(link_token=<redacted>, hosted_link_url=<redacted>, "
            f"expires_at={self.expires_at!r}, url_lifetime_seconds={self.url_lifetime_seconds!r})"
        )


class LinkSessionShape(StrEnum):
    """The observed shape of ``link_sessions`` in a ``/link/token/get`` reply.

    Seven values because the SDK can produce seven distinguishable states and
    **task 06a's F7 criterion 2 is precisely that the not-ready ones are
    asserted against the real API rather than guessed.** Collapsing the first
    three into one "not ready" would destroy the evidence the criterion asks
    for: a poller written against ``link_sessions == []`` and a Plaid that omits
    the key entirely both "work" until the day the shape changes.

    **There is no single "the pre-completion shape", and this enum is the reason
    that is sayable.** Criterion 2 splits into **2a** (minted, nobody has opened
    the URL — measured, and it is :attr:`SESSIONS_ABSENT`) and **2b** (opened and
    unfinished — *not measured*, because reaching it needs a browser). Which of
    these values 2b returns is an open question, and a wrapper that answered
    ready/not-ready would have made it unaskable.

    Verified against the installed SDK rather than assumed, because a
    permissive model already produced one bug on this seam
    (:meth:`PlaidClient.first_institution_supporting`): an unset attribute
    raises ``ApiAttributeError`` on direct access and is absent to ``getattr``,
    an explicit null reads back as ``None``, and an empty list reads back as
    ``[]``. All three are therefore reachable and tellable apart here.

    The last four were three until review round 1 of PR #58, which named two
    collapses the first cut had made. ``LinkTokenGetSessionsResponse`` types
    ``finished_at`` as ``datetime | None``, so a **live** session and an
    **exited** one are distinguishable in the reply and were nonetheless both
    reported as ``NO_ITEM_ADDED`` — that is the state ``07a`` observes while the
    owner is inside Link, and §7's response-driven transitions cannot be written
    against a shape that cannot see it. And an Item-add result whose
    ``public_token`` is missing was reported as ``NO_ITEM_ADDED`` too, i.e. "a
    slot was spent and we cannot see its token" was reported as "nothing was
    added" — the one direction **F2a** makes unrecoverable.
    """

    #: The response carried no ``link_sessions`` key at all.
    SESSIONS_ABSENT = "SESSIONS_ABSENT"
    #: The key was present and null.
    SESSIONS_NULL = "SESSIONS_NULL"
    #: The key was present and empty: Plaid reported a session list with none in it.
    NO_SESSIONS = "NO_SESSIONS"
    #: At least one session has no ``finished_at`` — someone is inside Link now.
    SESSION_IN_PROGRESS = "SESSION_IN_PROGRESS"
    #: Every session has concluded and none of them added an Item: an exit.
    NO_ITEM_ADDED = "NO_ITEM_ADDED"
    #: Every Item-add result Plaid reported carries its ``public_token``.
    ITEM_ADDED = "ITEM_ADDED"
    #: An Item was added and at least one of its ``public_token``\\ s is missing.
    ITEM_ADDED_TOKEN_ABSENT = "ITEM_ADDED_TOKEN_ABSENT"


@dataclass(frozen=True, slots=True)
class LinkSessionRecord:
    """One entry of ``link_sessions``, kept whole rather than flattened.

    The first cut of :class:`LinkSessionPoll` returned parallel tuples of
    session ids and public tokens, which threw away **which token belongs to
    which session** — and one reply can describe several sessions. Since each
    token is one spent Item slot (**F2a**), that association is what lets a
    later reader say *which* attempt spent *which* slot; parallel tuples of
    different lengths cannot say it at all.

    ``started_at`` / ``finished_at`` are here because **measurement (i) derives
    its deadline from them**: the hosted URL's lifetime is our number and the
    link token's expiry is Plaid's (see :class:`HostedLinkToken`), and the only
    way to measure the first against a real session is to see when that session
    began and ended. A poll that dropped them could not support the measurement
    it exists for.

    ``item_add_results`` counts what Plaid reported, which is **not** the same
    as ``len(public_tokens)``: see :attr:`tokens_missing`.

    **An absent ``finished_at`` and a null one are deliberately not told apart**
    — unlike ``link_sessions`` itself two levels up, where the same module
    insists on the distinction. It is not an inconsistency: there, absent means
    "not the reply shape we understand" and null means "Plaid affirmatively says
    none", and a measurement hangs on which. Here both mean *this reply states no
    finish instant*, a caller does the same thing for either — keep waiting — and
    that is the safe direction, because it is the one that never reports a
    possibly-spent slot as an abandonment. A value that is present and
    *unreadable* is neither, and raises.
    """

    session_id: str | None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    public_tokens: tuple[str, ...] = ()
    item_add_results: int = 0

    @property
    def finished(self) -> bool:
        """Whether Plaid reported this session as concluded.

        A missing ``finished_at`` is read as *not finished*, which is the safe
        direction: calling a live session concluded would let a poller stop
        while the owner is still inside Link, and Hosted Link produces no other
        signal that would correct it.
        """
        return self.finished_at is not None

    @property
    def tokens_missing(self) -> int:
        """Item-add results whose ``public_token`` this reply did not carry.

        Non-zero means a slot is spent and we are holding no handle to it. It is
        counted rather than inferred so the fact survives into
        :attr:`LinkSessionPoll.shape` instead of being read as "nothing here".
        """
        return max(self.item_add_results - len(self.public_tokens), 0)

    def __repr__(self) -> str:
        # `public_token` is a bearer credential for an already-spent slot and
        # `session_id` is Plaid's handle for one of the owner's Link attempts.
        # The two instants are rendered rather than redacted, and that is a
        # decision rather than an oversight: they are measurement (i)'s raw
        # material, they name no institution and no person, and a transcript
        # that hid them could not show the deadline it was run to measure.
        return (
            "LinkSessionRecord(session_id=<redacted>, "
            f"started_at={self.started_at!r}, finished_at={self.finished_at!r}, "
            f"public_tokens=<{len(self.public_tokens)} redacted>, "
            f"item_add_results={self.item_add_results})"
        )


@dataclass(frozen=True, slots=True)
class LinkSessionPoll:
    """One ``/link/token/get`` answer, as evidence rather than as a verdict.

    ``shape`` is what the reply looked like; ``sessions`` is the reply itself,
    one record per session. A caller decides it is done by asking for a token,
    never by asking whether the poll "succeeded" — that is the distinction
    **F7** turns on, since Hosted Link has no frontend integration and this
    reply is the only signal a completed session produces.

    **The shape names the most consequential fact; the records carry the rest.**
    Precedence, highest first: a spent slot whose token is missing, a spent slot
    whose token is in hand, a session still open, all sessions concluded with
    nothing added. It is ordered by what a reader must not miss rather than by
    what happened last — the top two are permanent and the bottom two are not.
    """

    shape: LinkSessionShape
    sessions: tuple[LinkSessionRecord, ...] = ()

    @property
    def public_tokens(self) -> tuple[str, ...]:
        """Every token in the reply, flattened, for the caller that exchanges them.

        Derived from :attr:`sessions` rather than stored beside it, so the two
        can never disagree. Every one of them is a slot already spent (**F2a**);
        returning the first and dropping the rest would silently lose Items the
        owner has already paid for.
        """
        return tuple(token for session in self.sessions for token in session.public_tokens)

    @property
    def session_ids(self) -> tuple[str, ...]:
        return tuple(s.session_id for s in self.sessions if s.session_id is not None)

    @property
    def item_added(self) -> bool:
        """Whether an Item slot has been spent — **not** whether one can be exchanged.

        True for ``ITEM_ADDED_TOKEN_ABSENT`` as well, because the slot is gone
        either way and that is the fact the Item budget is kept against. A
        caller that wants something to exchange asks for :attr:`public_tokens`
        and gets an empty tuple, rather than being told nothing happened.
        """
        return self.shape in (
            LinkSessionShape.ITEM_ADDED,
            LinkSessionShape.ITEM_ADDED_TOKEN_ABSENT,
        )

    @property
    def tokens_missing(self) -> int:
        return sum(session.tokens_missing for session in self.sessions)

    def __repr__(self) -> str:
        return (
            f"LinkSessionPoll(shape={self.shape.value}, "
            f"sessions=<{len(self.sessions)} redacted>, "
            f"public_tokens=<{len(self.public_tokens)} redacted>, "
            f"tokens_missing={self.tokens_missing})"
        )


@dataclass(frozen=True, slots=True)
class HoldingsObservation:
    """What ``/investments/holdings/get`` returned, as observations.

    One call, two lists: §8.1's source clock is split across them
    (``institution_price_as_of`` on a holding, ``close_price_as_of`` on a
    security), so both have to be observed or the clock question is half
    answered.
    """

    holdings: RecordSet
    securities: RecordSet


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


def _expires_at(response: Any, *, step: str) -> datetime | None:
    """``expiration`` as an aware UTC instant, or ``None`` when Plaid sent none.

    ``None`` is kept meaning exactly one thing. A value that is present but
    unreadable — a naive datetime, or something that is not a datetime at all —
    is a failed call, not a missing expiry: coercing it would invent a deadline,
    and folding it into ``None`` would report "Plaid gave no expiry" about a
    reply that gave one. Naive is refused rather than assumed UTC per
    ``AGENTS.md``; the sibling project shipped a "no notification" bug that came
    down to exactly that assumption.
    """
    value = getattr(response, "expiration", _MISSING)
    if value is _MISSING or value is None:
        return None
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise PlaidCallError(f"{step} returned an expiration that is not an aware datetime")
    return value.astimezone(UTC)


def _session_instant(session: Any, field: str) -> datetime | None:
    """``started_at`` / ``finished_at`` as an aware instant, or ``None``.

    Same rule as :func:`_expires_at` and for the same reason, one level in: the
    SDK types both as ``datetime``, so anything else is an unreadable reply
    rather than a timestamp to coerce. Naive is refused instead of assumed UTC —
    **measurement (i) subtracts these two**, and a wrong offset there does not
    look like an error, it looks like a URL lifetime that is hours off.
    """
    value = getattr(session, field, _MISSING)
    if value is _MISSING or value is None:
        return None
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise PlaidCallError(f"link/token/get returned a {field} that is not an aware datetime")
    return value.astimezone(UTC)


def _link_session_record(session: Any) -> LinkSessionRecord:
    """One ``link_sessions`` entry, with nothing about it inferred.

    ``item_add_results`` counts the results Plaid reported and
    ``public_tokens`` collects only the ones that carried a token, so the two
    disagree exactly when a spent slot has no visible handle. That gap is
    :attr:`LinkSessionRecord.tokens_missing`, and keeping it is the whole point:
    the first cut counted only tokens, which made "an Item was added and its
    token is missing" indistinguishable from "nothing was added".
    """
    results = getattr(session, "results", None)
    added = list(getattr(results, "item_add_results", None) or []) if results is not None else []
    tokens = [
        token
        for token in (cast("str | None", getattr(one, "public_token", None)) for one in added)
        if token
    ]
    return LinkSessionRecord(
        session_id=cast("str | None", getattr(session, "link_session_id", None)) or None,
        started_at=_session_instant(session, "started_at"),
        finished_at=_session_instant(session, "finished_at"),
        public_tokens=tuple(tokens),
        item_add_results=len(added),
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

    def __init__(self, credentials: PlaidCredentials, *, api: _PlaidApi | None = None) -> None:
        self._credentials = credentials
        self._api = api if api is not None else _build_api(credentials)

    @property
    def environment(self) -> PlaidEnvironment:
        return self._credentials.environment

    def _call(self, step: str, method: Any, request: Any) -> Any:
        """Every raising call goes through here, so every one redacts alike."""
        try:
            return method(request)
        except urllib3.exceptions.LocationValueError:
            # Our bug, not the environment's — the URL *we* built is not a URL.
            # The same carve-out `item_get` documents below: reported as a call
            # failure it would read as Plaid being unreachable.
            raise
        except ApiException as exc:
            reference = _request_id_of(exc)
            where = (
                f"look it up by request id {reference}"
                if reference is not None
                else "the body carried no request id"
            )
            raise PlaidCallError(
                f"{step} failed: Plaid returned HTTP {exc.status} "
                f"(body not shown — {where} in the Plaid dashboard)"
            ) from None
        except _TRANSPORT_ERRORS as exc:
            raise PlaidCallError(f"{step} failed: {type(exc).__name__}") from None

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

    def first_institution_supporting(
        self, *, products: Sequence[str], country_codes: Sequence[str]
    ) -> str:
        """``/institutions/get`` — ask Plaid which institutions support a product.

        Returns an ``institution_id`` and nothing else. Discovery rather than a
        constant is ``AGENTS.md`` rule 0: a hardcoded institution is a bug even
        when the institution is a fake bank, and asking is also more honest —
        it establishes that an institution supporting these products exists,
        instead of asserting one that might not.

        **``products`` goes inside ``options``, and the first live call is what
        proved it.** This method used to pass ``products=`` at the top level,
        where ``/institutions/get`` does not define it, and Plaid answered HTTP
        400. It survived every offline test because
        ``InstitutionsGetRequest.additional_properties_type`` accepts arbitrary
        attributes: the SDK took the undeclared keyword without complaint,
        serialised it as a top-level JSON key, and the test then read
        ``request.products`` back and found what it had just set. An assertion
        that round-trips through a permissive model cannot fail, so it confirmed
        the bug instead of catching it. The regression test pins the *serialised*
        request now, which is the only form Plaid ever sees.
        """
        request = InstitutionsGetRequest(
            count=1,
            offset=0,
            country_codes=[CountryCode(code) for code in country_codes],
            options=InstitutionsGetRequestOptions(
                products=[Products(name) for name in products],
            ),
        )
        response = self._call("institutions/get", self._api.institutions_get, request)
        institutions = getattr(response, "institutions", None) or []
        if not institutions:
            raise PlaidCallError(
                f"institutions/get returned no institution supporting {', '.join(products)} "
                f"in {', '.join(country_codes)}"
            )
        institution_id = cast("str | None", getattr(institutions[0], "institution_id", None))
        if not institution_id:
            raise PlaidCallError("institutions/get returned an institution with no institution_id")
        return institution_id

    def sandbox_public_token_create(
        self, *, institution_id: str, products: Sequence[str], username: str, password: str
    ) -> str:
        """``/sandbox/public_token/create`` — Plaid's documented *Link bypass*.

        It mints a fresh Sandbox Item and a ``public_token`` with **the Link UI
        never opening at all**, so what comes back is not a completed Link and
        must not be recorded as one. A real Hosted Link is task ``06a``'s to
        prove; describing the bypass as the thing it bypasses would leave ``06a``
        verifying something a transcript had already claimed.

        **Refuses outside Sandbox, before the request object is built.** The
        endpoint does not exist on ``production.plaid.com``, so this guard is
        not what stops a Production call from succeeding — it is what stops one
        from being *attempted* with the master credential attached (§15, F2).
        A guard that only lives in the caller is one the next caller forgets.
        """
        if self.environment is not PlaidEnvironment.SANDBOX:
            raise PlaidCallError(
                "sandbox/public_token/create refuses to run against "
                f"{self.environment.value!r}: Link is rehearsed in Sandbox precisely so "
                "that Production is never where it is tried out (task 06, F2a)"
            )
        request = SandboxPublicTokenCreateRequest(
            institution_id=institution_id,
            initial_products=[Products(name) for name in products],
            options=SandboxPublicTokenCreateRequestOptions(
                override_username=username,
                override_password=password,
            ),
        )
        response = self._call(
            "sandbox/public_token/create", self._api.sandbox_public_token_create, request
        )
        public_token = cast("str | None", getattr(response, "public_token", None))
        if not public_token:
            raise PlaidCallError("sandbox/public_token/create returned no public_token")
        return public_token

    def link_token_create_hosted(
        self,
        *,
        client_user_id: str,
        client_name: str,
        products: Sequence[str],
        country_codes: Sequence[str],
        language: str,
        url_lifetime_seconds: int | None = None,
        completion_redirect_uri: str | None = None,
    ) -> HostedLinkToken:
        """``/link/token/create`` with ``hosted_link`` — the only Link this project opens.

        **No environment guard, deliberately.** Unlike
        :meth:`sandbox_public_token_create`, this call is *supposed* to run
        against Production: task ``08`` is the owner-run Production Link and it
        goes through here. The refusal that protects the Item budget lives at
        ``08``'s gates, not on this method, and adding one here would make the
        Production path depend on a branch no test could exercise.

        **Hosted Link, not a redirect flow.** Plaid states there is "no frontend
        integration required (or possible)", and ``completion_redirect_uri``
        carries no token — which is why ``/link/token/get`` exists at all and why
        **F7** is a hard gate on ``08``. The redirect is offered here for the
        owner-facing "you're done" page only; nothing reads a token out of it.

        ``hosted_link`` is a **declared** attribute of ``LinkTokenCreateRequest``
        in the installed SDK. That was checked rather than assumed:
        :meth:`first_institution_supporting` documents how this SDK silently
        accepts an *undeclared* keyword and serialises it at the wrong level,
        producing an HTTP 400 that every offline test passes. The test for this
        method pins the **serialised** request for the same reason.
        """
        hosted: dict[str, Any] = {}
        if url_lifetime_seconds is not None:
            hosted["url_lifetime_seconds"] = url_lifetime_seconds
        if completion_redirect_uri is not None:
            hosted["completion_redirect_uri"] = completion_redirect_uri
        request = LinkTokenCreateRequest(
            client_name=client_name,
            language=language,
            country_codes=[CountryCode(code) for code in country_codes],
            user=LinkTokenCreateRequestUser(client_user_id=client_user_id),
            products=[Products(name) for name in products],
            hosted_link=LinkTokenCreateHostedLink(**hosted),
        )
        response = self._call("link/token/create", self._api.link_token_create, request)
        link_token = cast("str | None", getattr(response, "link_token", None))
        hosted_link_url = cast("str | None", getattr(response, "hosted_link_url", None))
        if not link_token:
            raise PlaidCallError("link/token/create returned no link_token")
        if not hosted_link_url:
            # A link token with no hosted URL is a Link this program cannot
            # open: there is no frontend integration to fall back to. Treated as
            # a failed call rather than returned half-built, so nothing downstream
            # can hand the owner a session that does not exist.
            raise PlaidCallError(
                "link/token/create returned no hosted_link_url, so the session cannot be "
                "opened at all (Hosted Link has no frontend integration to fall back to)"
            )
        return HostedLinkToken(
            link_token=link_token,
            hosted_link_url=hosted_link_url,
            expires_at=_expires_at(response, step="link/token/create"),
            url_lifetime_seconds=url_lifetime_seconds,
        )

    def link_token_get(self, link_token: str) -> LinkSessionPoll:
        """``/link/token/get`` — the only way a completed Hosted Link reports itself.

        Returns what the reply *looked like*, never a bare "ready" boolean. The
        seven shapes of :class:`LinkSessionShape` are seven different facts, and
        the not-ready ones are what **F7** criterion 2 asserts against the live
        API — a poller whose "not ready" branch was written against a guessed
        fixture is exactly what that criterion exists to prevent. **Criterion 2
        has two halves**: the pre-start shape is measured
        (:attr:`LinkSessionShape.SESSIONS_ABSENT`), and the started-but-unfinished
        shape is not, because reaching it needs a browser. Callers must branch on
        the absence of a ``public_token``, not on the absent key.

        Absent and null are told apart here for the same reason
        :meth:`item_get` tells them apart for ``item.error``: absent means the
        reply was not the shape we understand, null means Plaid affirmatively
        reported nothing. Reading both as "no sessions yet" would make the
        measurement unable to fail.

        Every session is returned as a :class:`LinkSessionRecord` rather than
        flattened into parallel tuples. The shape is a summary *of* those
        records and is computed from them here, in one place, so that a caller
        reading ``poll.sessions`` and a caller reading ``poll.shape`` cannot
        reach different conclusions about the same reply.
        """
        request = LinkTokenGetRequest(link_token=link_token)
        response = self._call("link/token/get", self._api.link_token_get, request)
        sessions = getattr(response, "link_sessions", _MISSING)
        if sessions is _MISSING:
            return LinkSessionPoll(shape=LinkSessionShape.SESSIONS_ABSENT)
        if sessions is None:
            return LinkSessionPoll(shape=LinkSessionShape.SESSIONS_NULL)
        if not isinstance(sessions, list | tuple):
            raise PlaidCallError("link/token/get returned a link_sessions that is not a list")
        if not sessions:
            return LinkSessionPoll(shape=LinkSessionShape.NO_SESSIONS)

        records = tuple(_link_session_record(session) for session in sessions)
        if any(record.tokens_missing for record in records):
            shape = LinkSessionShape.ITEM_ADDED_TOKEN_ABSENT
        elif any(record.public_tokens for record in records):
            shape = LinkSessionShape.ITEM_ADDED
        elif any(not record.finished for record in records):
            shape = LinkSessionShape.SESSION_IN_PROGRESS
        else:
            shape = LinkSessionShape.NO_ITEM_ADDED
        return LinkSessionPoll(shape=shape, sessions=records)

    def item_public_token_exchange(self, public_token: str) -> ExchangedItem:
        """``/item/public_token/exchange`` — the short-lived token for the durable one.

        Both fields are required rather than optional: an ``access_token``
        without an ``item_id`` cannot be written down against anything, and
        **F2a** makes what has already happened at this point permanent — the
        slot was spent by the Link, not by this call. Half a result here is a
        stranded Item, so it is refused as a failure instead of returned as a
        partial success.

        The response's ``request_id`` is carried out with them. It is the only
        durable handle on *this* exchange that Plaid will recognise in a support
        ticket, and `07a` records it against the ``link_flow`` attempt when the
        response reaches the process — which it cannot do if this wrapper is the
        place the id stops. It is validated, never refused: see
        :class:`ExchangedItem`.
        """
        request = ItemPublicTokenExchangeRequest(public_token=public_token)
        response = self._call(
            "item/public_token/exchange", self._api.item_public_token_exchange, request
        )
        access_token = cast("str | None", getattr(response, "access_token", None))
        item_id = cast("str | None", getattr(response, "item_id", None))
        if not access_token or not item_id:
            raise PlaidCallError(
                "item/public_token/exchange returned no access_token or no item_id"
            )
        reference = cast("str | None", getattr(response, "request_id", None))
        if not isinstance(reference, str) or not _REQUEST_ID.match(reference):
            reference = None
        return ExchangedItem(access_token=access_token, item_id=item_id, request_id=reference)

    def accounts_balance_get(self, access_token: str, *, fields: Sequence[str]) -> RecordSet:
        """``/accounts/balance/get`` — observed, never returned.

        ``fields`` is the caller's list of the fields *it* needs (§10 for
        balances); what comes back is presence and type per field, so the
        figures never cross this seam at all.
        """
        response = self._call(
            "accounts/balance/get",
            self._api.accounts_balance_get,
            AccountsBalanceGetRequest(access_token=access_token),
        )
        accounts = list(getattr(response, "accounts", None) or [])
        return record_set("accounts", accounts, fields)

    def investments_holdings_get(
        self, access_token: str, *, holding_fields: Sequence[str], security_fields: Sequence[str]
    ) -> HoldingsObservation:
        """``/investments/holdings/get`` — both of its lists, observed."""
        response = self._call(
            "investments/holdings/get",
            self._api.investments_holdings_get,
            InvestmentsHoldingsGetRequest(access_token=access_token),
        )
        holdings = list(getattr(response, "holdings", None) or [])
        securities = list(getattr(response, "securities", None) or [])
        return HoldingsObservation(
            holdings=record_set("holdings", holdings, holding_fields),
            securities=record_set("securities", securities, security_fields),
        )


def _build_api(credentials: PlaidCredentials) -> _PlaidApi:
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
    return cast("_PlaidApi", plaid_api.PlaidApi(plaid.ApiClient(configuration)))
