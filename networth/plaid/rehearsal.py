"""The Sandbox rehearsal: Link, exchange, fetch — and what Sandbox actually returned.

Task ``06``. Sandbox is free and unlimited; Production slots are permanent and are
spent by a successful **Link**, not by the exchange (**F2a**). That asymmetry is the
whole reason this rehearsal exists: Sandbox is the only place the boundary can be
walked into without spending something that never comes back.

**This module refuses to run against Production, and the refusal is the point.**
``NETWORTH_ENV`` already selects the credential file, the items file and the database
*together* (§15, :mod:`networth.plaid.environment`), so a rehearsal cannot write into
the Production history by pointing at the wrong database. This adds the second half:
even handed Production credentials explicitly, the rehearsal stops. The task's "must
not" is one line — *touch Production* — and a guard that only lives in the caller is
a guard that the next caller forgets.

**No institution is named here.** ``AGENTS.md`` rule 0 forbids institution-specific
detail anywhere in this repository, and a Sandbox institution id would be exactly
that shape even though it identifies a fake bank. The institution is discovered at
runtime from ``/institutions/get``, which is also more honest: it asks Plaid which
institutions support the products this project needs rather than asserting one.

**What it observes, and why that is the deliverable.** The acceptance criterion is
not "the calls returned 200" — it is that the response was *inspected for the fields
net worth actually needs*. §8.1 derives every age from a **source clock**
(``institution_price_as_of``, ``close_price_as_of``), never from the fact that a call
succeeded, and §10 needs balances in minor units. Whether Sandbox supplies those
fields at all is an empirical question no document could answer, so this module
records presence and *type* for each one. **It never records a value:** the figures
are synthetic, but a module that prints balances is one edit away from printing real
ones, and the type is the part that matters anyway (§7 forbids float money).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import plaid
import urllib3.exceptions
from plaid.api import plaid_api
from plaid.exceptions import ApiException
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_request import InstitutionsGetRequest
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.products import Products
from plaid.model.sandbox_public_token_create_request import SandboxPublicTokenCreateRequest
from plaid.model.sandbox_public_token_create_request_options import (
    SandboxPublicTokenCreateRequestOptions,
)

from networth.plaid.environment import PlaidCredentials, PlaidEnvironment
from networth.tokenstore import SecretKind, TokenStore, new_flow_id

# The Sandbox test user. Plaid's Link UI accepts these at any Sandbox institution,
# and `/sandbox/public_token/create` takes the same pair through `options` — which is
# how a rehearsal completes a Link with `user_good`/`pass_good` without a human at a
# browser. They are not credentials: they are documented constants that unlock a fake
# bank, and they are worthless against `production.plaid.com`.
SANDBOX_USERNAME = "user_good"
SANDBOX_PASSWORD = "pass_good"

# What this project aggregates (§3): investment holdings and depository balances.
_REQUIRED_PRODUCTS = ("investments",)
_COUNTRY_CODES = ("US",)

# Same reasoning as `client.py`: the urllib3 family reaches us unwrapped, and it is
# neither an `OSError` nor an `ApiException`.
_TRANSPORT_ERRORS = (urllib3.exceptions.HTTPError, OSError)

_MISSING = object()

# The fields §8.1 and §10 actually need, as dotted paths into each response. The two
# `*_as_of` entries are the source clock — the reason this list exists at all, since
# §8.1 refuses to derive an age from a call's success.
_ACCOUNT_FIELDS = (
    "account_id",
    "name",
    "type",
    "subtype",
    "balances.current",
    "balances.available",
    "balances.limit",
    "balances.iso_currency_code",
    "balances.unofficial_currency_code",
)
_HOLDING_FIELDS = (
    "account_id",
    "security_id",
    "quantity",
    "institution_price",
    "institution_value",
    "cost_basis",
    "iso_currency_code",
    "institution_price_as_of",
)
_SECURITY_FIELDS = (
    "security_id",
    "ticker_symbol",
    "name",
    "type",
    "close_price",
    "close_price_as_of",
    "iso_currency_code",
    "is_cash_equivalent",
)


class RehearsalError(RuntimeError):
    """The rehearsal could not complete, with a reason that carries no secret."""


@dataclass(frozen=True, slots=True)
class FieldObservation:
    """One field net worth needs, and what Sandbox actually supplied for it.

    ``type_name`` is the Python type the SDK produced, never the value. It is the
    half that decides real work: §7 stores money as integer minor units, so a
    ``float`` here is a finding for task ``12`` rather than a detail.
    """

    path: str
    present: bool
    type_name: str | None

    @property
    def note(self) -> str:
        if not self.present:
            return "absent"
        return "null" if self.type_name is None else self.type_name


@dataclass(frozen=True, slots=True)
class RehearsalOutcome:
    """What one full Link -> exchange -> fetch cycle established."""

    environment: PlaidEnvironment
    institution_id: str
    item_id: str
    secret_ref: str
    database: Path
    account_count: int
    holding_count: int
    security_count: int
    accounts: tuple[FieldObservation, ...]
    holdings: tuple[FieldObservation, ...]
    securities: tuple[FieldObservation, ...]


class SandboxApi(Protocol):
    """Only the calls this rehearsal makes.

    Narrow for the same reason ``client.py``'s protocol is: it lets the tests drive
    every branch with synthetic responses instead of the network, and task ``06``
    inherits task ``05``'s rule that the suite makes no live call.
    """

    def institutions_get(self, institutions_get_request: Any) -> Any: ...
    def sandbox_public_token_create(self, sandbox_public_token_create_request: Any) -> Any: ...
    def item_public_token_exchange(self, item_public_token_exchange_request: Any) -> Any: ...
    def accounts_balance_get(self, accounts_balance_get_request: Any) -> Any: ...
    def investments_holdings_get(self, investments_holdings_get_request: Any) -> Any: ...


def _attribute(obj: Any, path: str) -> tuple[bool, str | None]:
    """Walk a dotted path, distinguishing "absent" from "present and null".

    For a source clock those are opposite facts and §8.1 turns on the difference:
    absent means Plaid does not supply the field for this product at all, null means
    Plaid supplies it and has no value — the first is a design constraint, the second
    is an ``UNKNOWN`` age. Collapsing them would be the exact mistake §8.1 exists to
    prevent, one layer down.
    """
    current: Any = obj
    for part in path.split("."):
        if current is None:
            return False, None
        nxt = getattr(current, part, _MISSING)
        if nxt is _MISSING:
            # `plaid-python` models raise for unset optional attributes on some
            # versions and return a sentinel on others; a mapping lookup covers the
            # dict-shaped responses the tests use without inventing a value.
            if isinstance(current, dict) and part in current:
                nxt = current[part]
            else:
                return False, None
        current = nxt
    if current is None:
        return True, None
    return True, type(current).__name__


def _observe(items: Sequence[Any], fields: Sequence[str]) -> tuple[FieldObservation, ...]:
    """Fold a list of records into one observation per field.

    A field counts as present when **any** record carried it, and its type is taken
    from the first record that supplied a non-null value. Reporting per-record would
    turn a synthetic fixture's row count into noise; the question being answered is
    "does Sandbox supply this field", not "how many rows came back".
    """
    observations = []
    for path in fields:
        present = False
        type_name: str | None = None
        for item in items:
            item_present, item_type = _attribute(item, path)
            present = present or item_present
            if type_name is None and item_type is not None:
                type_name = item_type
        observations.append(FieldObservation(path=path, present=present, type_name=type_name))
    return tuple(observations)


def _plaid_failure(step: str, exc: Exception) -> RehearsalError:
    """Turn any Plaid or transport failure into one that names the step, not the data.

    ``ApiException``'s string form contains the response body and its headers. The
    body of a Plaid error is not a credential, but the same object is raised on the
    call that carries ``client_id`` and ``secret``, and this message is printed and
    pasted into PRs. The step and the exception type locate the failure; the request
    id is Plaid's own handle for the rest.
    """
    if isinstance(exc, ApiException):
        return RehearsalError(
            f"{step} failed: Plaid returned HTTP {exc.status} "
            f"(body not shown — read it from the Plaid dashboard by request id)"
        )
    return RehearsalError(f"{step} failed: {type(exc).__name__}")


class SandboxRehearsal:
    """One Link -> exchange -> fetch cycle, against Sandbox and nowhere else."""

    def __init__(
        self,
        credentials: PlaidCredentials,
        *,
        token_store: TokenStore,
        database: Path,
        api: SandboxApi | None = None,
    ) -> None:
        if credentials.environment is not PlaidEnvironment.SANDBOX:
            # Not an assertion and not a caller's responsibility. The task's one
            # "must not" is *touch Production*, and F2a makes the failure permanent:
            # a successful Link spends a lifetime slot whether or not anyone meant
            # it. Refusing in the constructor means no code path exists in which a
            # Production credential reaches `/sandbox/public_token/create`.
            raise RehearsalError(
                "the rehearsal refuses to run against "
                f"{credentials.environment.value!r}: it exists so that Production is "
                "never the place a Link flow is tried out (task 06, F2a)"
            )
        self._credentials = credentials
        self._token_store = token_store
        self._database = database
        self._api = api if api is not None else _build_api(credentials)

    def _call(self, step: str, method: Any, request: Any) -> Any:
        try:
            return method(request)
        except urllib3.exceptions.LocationValueError:
            # Our bug, not the environment's — the URL we built is not a URL. Same
            # carve-out `client.py` documents: reported as a rehearsal failure it
            # would look like Plaid being unreachable.
            raise
        except (ApiException, *_TRANSPORT_ERRORS) as exc:
            raise _plaid_failure(step, exc) from None

    def choose_institution(self) -> str:
        """Ask Plaid which institutions support what this project aggregates.

        Discovered rather than hardcoded (``AGENTS.md`` rule 0). Taking the first
        match is deliberate: any institution that supports the products is equally
        valid for a rehearsal, and choosing among them by name would reintroduce
        exactly the institution-specific knowledge the rule forbids.
        """
        request = InstitutionsGetRequest(
            count=1,
            offset=0,
            country_codes=[CountryCode(code) for code in _COUNTRY_CODES],
            products=[Products(name) for name in _REQUIRED_PRODUCTS],
        )
        response = self._call("institutions/get", self._api.institutions_get, request)
        institutions = getattr(response, "institutions", None) or []
        if not institutions:
            raise RehearsalError(
                "institutions/get returned no institution supporting "
                f"{', '.join(_REQUIRED_PRODUCTS)} in {', '.join(_COUNTRY_CODES)}"
            )
        institution_id = cast("str | None", getattr(institutions[0], "institution_id", None))
        if not institution_id:
            raise RehearsalError("institutions/get returned an institution with no institution_id")
        return institution_id

    def create_public_token(self, institution_id: str) -> str:
        """Complete a Sandbox Link as ``user_good``/``pass_good``.

        ``/sandbox/public_token/create`` is the automatable form of the Link the
        owner will run by hand in Production: same institution, same test user, same
        `public_token` on the other side. What it deliberately does **not** rehearse
        is Hosted Link's completed-session retrieval — that is task ``06a``'s job
        (F7, §16), and it is called out here so this method is not mistaken for
        proof of the Production path.
        """
        request = SandboxPublicTokenCreateRequest(
            institution_id=institution_id,
            initial_products=[Products(name) for name in _REQUIRED_PRODUCTS],
            options=SandboxPublicTokenCreateRequestOptions(
                override_username=SANDBOX_USERNAME,
                override_password=SANDBOX_PASSWORD,
            ),
        )
        response = self._call(
            "sandbox/public_token/create", self._api.sandbox_public_token_create, request
        )
        public_token = cast("str | None", getattr(response, "public_token", None))
        if not public_token:
            raise RehearsalError("sandbox/public_token/create returned no public_token")
        return public_token

    def exchange(self, public_token: str) -> tuple[str, str, str]:
        """Exchange for an ``access_token`` and write it down before anything else.

        §14a's ordering, rehearsed rather than described: the ``access_token`` is
        durable through :class:`TokenStore` *before* the caller records an item, so
        a crash in between leaves an orphan record and never a stranded slot. In
        Sandbox a stranded slot costs nothing, which is precisely why the ordering
        should be exercised here rather than first attempted in Production.
        """
        request = ItemPublicTokenExchangeRequest(public_token=public_token)
        response = self._call(
            "item/public_token/exchange", self._api.item_public_token_exchange, request
        )
        access_token = cast("str | None", getattr(response, "access_token", None))
        item_id = cast("str | None", getattr(response, "item_id", None))
        if not access_token or not item_id:
            raise RehearsalError(
                "item/public_token/exchange returned no access_token or no item_id"
            )
        secret_ref = self._token_store.put(
            SecretKind.ACCESS_TOKEN,
            new_flow_id(),
            access_token,
            item_id=item_id,
        )
        return access_token, item_id, secret_ref

    def fetch(self, access_token: str) -> tuple[Any, Any]:
        """Both fetches the product needs: balances (§10) and holdings (§3)."""
        balances = self._call(
            "accounts/balance/get",
            self._api.accounts_balance_get,
            AccountsBalanceGetRequest(access_token=access_token),
        )
        holdings = self._call(
            "investments/holdings/get",
            self._api.investments_holdings_get,
            InvestmentsHoldingsGetRequest(access_token=access_token),
        )
        return balances, holdings

    def run(self) -> RehearsalOutcome:
        """The whole cycle, in the order Production will run it."""
        institution_id = self.choose_institution()
        public_token = self.create_public_token(institution_id)
        access_token, item_id, secret_ref = self.exchange(public_token)
        balances, holdings = self.fetch(access_token)

        accounts = list(getattr(balances, "accounts", None) or [])
        holding_records = list(getattr(holdings, "holdings", None) or [])
        securities = list(getattr(holdings, "securities", None) or [])

        return RehearsalOutcome(
            environment=self._credentials.environment,
            institution_id=institution_id,
            item_id=item_id,
            secret_ref=secret_ref,
            database=self._database,
            account_count=len(accounts),
            holding_count=len(holding_records),
            security_count=len(securities),
            accounts=_observe(accounts, _ACCOUNT_FIELDS),
            holdings=_observe(holding_records, _HOLDING_FIELDS),
            securities=_observe(securities, _SECURITY_FIELDS),
        )


def _build_api(credentials: PlaidCredentials) -> SandboxApi:
    """The real SDK client, wired to the host the environment selected.

    Same construction as ``client.py``: the credential and the host come from one
    object, so no arrangement of this code sends a Sandbox secret to Production or
    the reverse (§15).
    """
    configuration = plaid.Configuration(
        host=credentials.environment.api_host,
        api_key={"clientId": credentials.client_id, "secret": credentials.secret},
    )
    return cast("SandboxApi", plaid_api.PlaidApi(plaid.ApiClient(configuration)))
