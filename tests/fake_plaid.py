"""A synthetic Plaid SDK, shared by the client and rehearsal suites.

No test in this repository makes a live call (task 05's rule, task 06 inherits it),
so every branch is driven from here. It lives in its own module rather than in one
of the two suites because both drive the *same* seam:
:class:`~networth.plaid.client.PlaidClient` is the only thing that talks to the SDK,
and the rehearsal exercises it through that seam rather than around it.

Every method records its request and answers with a default that can be overridden
per call — including with an exception instance, which is raised instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

# Not a real institution and not derived from one: `AGENTS.md` rule 0 keeps
# institution-specific detail out of this repository, including its tests.
INSTITUTION = "ins_synthetic_0"
PUBLIC_TOKEN = "public-sandbox-synthetic"
ACCESS_TOKEN = "access-sandbox-synthetic"
ITEM_ID = "item-synthetic"
LINK_TOKEN = "link-sandbox-synthetic"
HOSTED_LINK_URL = "https://example.invalid/hosted-link/synthetic"
LINK_TOKEN_EXPIRATION = datetime(2026, 9, 8, 21, 0, tzinfo=UTC)
LINK_SESSION_ID = "link-session-synthetic"
# Distinct from the generic `req-synthetic` the other endpoints return, so a test
# asserting the exchange's id travelled cannot be satisfied by some *other*
# call's id arriving instead. Provenance is the whole point of capturing it.
EXCHANGE_REQUEST_ID = "req-exchange-synthetic"
# Measurement (i) subtracts these two, so they are far enough apart to tell a
# real interval from a zero one.
LINK_SESSION_STARTED = datetime(2026, 9, 8, 20, 0, tzinfo=UTC)
LINK_SESSION_FINISHED = datetime(2026, 9, 8, 20, 4, tzinfo=UTC)


def link_sessions_response(
    *,
    sessions: Any,
    link_token: str = LINK_TOKEN,
) -> Any:
    """A ``/link/token/get`` reply carrying whatever session list is passed.

    Takes the list rather than building one so a test can supply the empty list,
    an explicit ``None``, or sessions that are still open, that concluded with
    nothing, or that added an Item with or without its token. Those are six
    different observable shapes and the seventh — the key absent entirely — is
    produced by *not* calling this helper at all.
    """
    return SimpleNamespace(
        link_token=link_token, link_sessions=sessions, request_id="req-synthetic"
    )


def completed_session(
    *,
    public_tokens: Sequence[str] = (PUBLIC_TOKEN,),
    session_id: str = LINK_SESSION_ID,
    started_at: datetime | None = LINK_SESSION_STARTED,
    finished_at: datetime | None = LINK_SESSION_FINISHED,
    untokened_results: int = 0,
) -> Any:
    """One finished Link session that added an Item per token given.

    ``untokened_results`` appends Item-add results with **no** ``public_token``.
    Plaid's model types that field as ``str`` but the SDK does not require it, so
    a result whose token is absent is reachable — and it means a slot was spent
    that we hold no handle to. The fixture can produce it because the wrapper has
    to be able to report it (PR #58 review, finding 2).
    """
    added = [
        SimpleNamespace(public_token=token, institution=None, accounts=[])
        for token in public_tokens
    ]
    added += [SimpleNamespace(institution=None, accounts=[]) for _ in range(untokened_results)]
    return SimpleNamespace(
        link_session_id=session_id,
        started_at=started_at,
        finished_at=finished_at,
        results=SimpleNamespace(item_add_results=added),
    )


def session_without_item(
    *,
    session_id: str = LINK_SESSION_ID,
    started_at: datetime | None = LINK_SESSION_STARTED,
    finished_at: datetime | None = LINK_SESSION_FINISHED,
) -> Any:
    """A session that concluded without adding anything — an exit."""
    return SimpleNamespace(
        link_session_id=session_id,
        started_at=started_at,
        finished_at=finished_at,
        results=None,
    )


def session_in_progress(*, session_id: str = LINK_SESSION_ID) -> Any:
    """A session Plaid has started and not finished: someone is inside Link now.

    ``finished_at`` is typed ``datetime | None`` by the SDK, so this is a state
    the real API can return, and it is the one ``07a`` observes while the owner
    is completing the hosted URL.
    """
    return SimpleNamespace(
        link_session_id=session_id,
        started_at=LINK_SESSION_STARTED,
        finished_at=None,
        results=None,
    )


def _accounts_response() -> Any:
    return SimpleNamespace(
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
                    # §8.1's realtime-balance source clock. Supplied here precisely
                    # because Plaid documents it as appearing "only when the
                    # institution is `ins_128026`": a fake that omitted it could not
                    # tell "the rehearsal never looked" from "Sandbox does not send
                    # it", and those are the two answers the live run has to
                    # distinguish.
                    last_updated_datetime=datetime(2026, 9, 7, 14, 30, tzinfo=UTC),
                ),
            )
        ]
    )


def _holdings_response() -> Any:
    return SimpleNamespace(
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
                # The clock §8.1 *prefers* over `institution_price_as_of`, and the
                # one it warns "may contain default time values (such as 00:00:00)".
                # Present-and-typed here so the suite can pin that the rehearsal asks
                # for it; whether Sandbox answers is the live run's question.
                institution_price_datetime=datetime(2026, 9, 5, 21, 0, tzinfo=UTC),
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
    )


class FakeSandboxApi:
    """Every SDK call the Link rehearsal makes, recorded, with synthetic answers."""

    def __init__(self, **overrides: Any) -> None:
        self.requests: list[tuple[str, Any]] = []
        self._overrides = overrides

    @property
    def called(self) -> list[str]:
        return [name for name, _ in self.requests]

    def request_for(self, name: str) -> Any:
        return dict(self.requests)[name]

    def _answer(self, name: str, default: Any, request: Any) -> Any:
        self.requests.append((name, request))
        answer = self._overrides.get(name, default)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def item_get(self, item_get_request: Any) -> Any:
        # Present because the SDK protocol has it, and raising because no test that
        # uses this fake should be reaching the item poller: a silently-answered
        # call is how a test stops testing what its name says.
        raise AssertionError("the Link rehearsal must not call /item/get")

    def institutions_get(self, institutions_get_request: Any) -> Any:
        return self._answer(
            "institutions_get",
            SimpleNamespace(institutions=[SimpleNamespace(institution_id=INSTITUTION)]),
            institutions_get_request,
        )

    def sandbox_public_token_create(self, sandbox_public_token_create_request: Any) -> Any:
        return self._answer(
            "sandbox_public_token_create",
            SimpleNamespace(public_token=PUBLIC_TOKEN),
            sandbox_public_token_create_request,
        )

    def item_public_token_exchange(self, item_public_token_exchange_request: Any) -> Any:
        return self._answer(
            "item_public_token_exchange",
            # `request_id` is a *required* field of the SDK's response model —
            # verified against the locked SDK, not assumed — so a fake that
            # omitted it would be a shape Plaid cannot return, and would let the
            # wrapper's absent-id branch look like the ordinary case.
            SimpleNamespace(
                access_token=ACCESS_TOKEN,
                item_id=ITEM_ID,
                request_id=EXCHANGE_REQUEST_ID,
            ),
            item_public_token_exchange_request,
        )

    def link_token_create(self, link_token_create_request: Any) -> Any:
        return self._answer(
            "link_token_create",
            SimpleNamespace(
                link_token=LINK_TOKEN,
                hosted_link_url=HOSTED_LINK_URL,
                expiration=LINK_TOKEN_EXPIRATION,
                request_id="req-synthetic",
            ),
            link_token_create_request,
        )

    def link_token_get(self, link_token_get_request: Any) -> Any:
        # The default is the **pre-completion** shape, which is the one task 06a
        # (F7 criterion 2) has to assert against the live API: no `link_sessions`
        # key at all. `SimpleNamespace` reproduces that faithfully — an unset
        # attribute is absent to `getattr` here exactly as it is on the SDK model,
        # where direct access raises `ApiAttributeError` (checked against the
        # installed SDK, not assumed).
        return self._answer(
            "link_token_get",
            SimpleNamespace(link_token=LINK_TOKEN, request_id="req-synthetic"),
            link_token_get_request,
        )

    def accounts_balance_get(self, accounts_balance_get_request: Any) -> Any:
        return self._answer(
            "accounts_balance_get", _accounts_response(), accounts_balance_get_request
        )

    def investments_holdings_get(self, investments_holdings_get_request: Any) -> Any:
        return self._answer(
            "investments_holdings_get", _holdings_response(), investments_holdings_get_request
        )
