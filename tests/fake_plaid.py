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

from types import SimpleNamespace
from typing import Any

# Not a real institution and not derived from one: `AGENTS.md` rule 0 keeps
# institution-specific detail out of this repository, including its tests.
INSTITUTION = "ins_synthetic_0"
PUBLIC_TOKEN = "public-sandbox-synthetic"
ACCESS_TOKEN = "access-sandbox-synthetic"
ITEM_ID = "item-synthetic"


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
            SimpleNamespace(access_token=ACCESS_TOKEN, item_id=ITEM_ID),
            item_public_token_exchange_request,
        )

    def accounts_balance_get(self, accounts_balance_get_request: Any) -> Any:
        return self._answer(
            "accounts_balance_get", _accounts_response(), accounts_balance_get_request
        )

    def investments_holdings_get(self, investments_holdings_get_request: Any) -> Any:
        return self._answer(
            "investments_holdings_get", _holdings_response(), investments_holdings_get_request
        )
