"""The Sandbox rehearsal: item, exchange, fetch — and what Sandbox actually returned.

Task ``06``. Sandbox is free and unlimited; Production slots are permanent and are
spent by a successful **Link**, not by the exchange (**F2a**). That asymmetry is the
whole reason this rehearsal exists: Sandbox is the only place the boundary can be
walked into without spending something that never comes back.

**It does not run Link, and the distinction is load-bearing.**
``/sandbox/public_token/create`` is documented by Plaid under *"Bypassing Link"* — it
returns a ``public_token`` for a fresh Sandbox Item without the Link UI opening at
all. What is rehearsed here is therefore every step *after* a ``public_token``
exists. A completed **Hosted Link** is task ``06a``'s to prove; ``06``'s value is that
the exchange, the ``TokenStore`` ordering and the fetch are walked before Production,
and that is true whether or not a browser was involved.

**This is orchestration, not a second Plaid client.** ``DESIGN.md`` §5 puts every raw
endpoint call on :class:`~networth.plaid.client.PlaidClient`; what lives here is the
order the calls run in, the Sandbox-only guard, the ``TokenStore``-before-``item``
ordering, and the choice of which fields are worth observing. The protocol below is
deliberately narrow so that task ``05``'s item-status fakes never have to grow methods
they do not use. *(Codex ruled on this split on 2026-09-07, after the first version of
this module built its own SDK client.)*

**It refuses to run against Production, and the refusal is the point.**
``NETWORTH_ENV`` already selects the credential file, the items file and the database
*together* (§15, :mod:`networth.plaid.environment`), so a rehearsal cannot write into
the Production history by pointing at the wrong database. This adds the second half:
even handed Production credentials explicitly, the rehearsal stops before it builds a
single request.

**No institution is named here.** ``AGENTS.md`` rule 0 forbids institution-specific
detail anywhere in this repository, and a Sandbox institution id would be exactly that
shape even though it identifies a fake bank. The institution is discovered at runtime
from ``/institutions/get``.

**What it observes, and why that is the deliverable.** The acceptance criterion is not
"the calls returned 200" — it is that the response was inspected for the fields net
worth actually needs. §8.1 derives every age from a **source clock**
(``institution_price_as_of``, ``close_price_as_of``), never from the fact that a call
succeeded, and §10 needs balances in minor units. Whether Sandbox supplies those fields
at all is an empirical question no document could answer, so the client records presence
and *type* for each one and never a value (:mod:`networth.plaid.observation`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from networth.plaid.client import ExchangedItem, HoldingsObservation, PlaidClient
from networth.plaid.environment import PlaidCredentials, PlaidEnvironment
from networth.plaid.observation import RecordSet
from networth.tokenstore import SecretKind, TokenStore, new_flow_id

# The Sandbox test user. Plaid's Link UI accepts these at any Sandbox institution, and
# `/sandbox/public_token/create` takes the same pair through `options`.
#
# **It does not run Link.** Plaid files that endpoint under "Bypassing Link", and
# Sandbox Studio labels it "Skip Link": it mints a Sandbox Item and a `public_token`
# without the Link UI ever opening. So what this module rehearses is everything *after*
# the token exists — exchange, `TokenStore` ordering, fetch, and what the response
# contains. Proving a real Hosted Link completes is task `06a`, which is the row that
# owns it. Saying "Link completed" here would have recorded a bypass as the thing it
# bypasses. (Codex, PR #49 pre-execution review, 2026-09-07.)
#
# They are not credentials: they are documented constants that unlock a fake bank, and
# they are worthless against `production.plaid.com`.
SANDBOX_USERNAME = "user_good"
SANDBOX_PASSWORD = "pass_good"

# What this project aggregates (§3): investment holdings and depository balances.
REQUIRED_PRODUCTS = ("investments",)
COUNTRY_CODES = ("US",)

# The fields §8.1 and §10 actually need, as dotted paths into each response. The
# source-clock entries are the reason these lists exist at all, since §8.1 refuses to
# derive an age from a call's success.
#
# §8.1's table names **four** clocks, not two, and the two extra ones are the fields
# most likely to be absent — which is exactly why a rehearsal has to look for them:
#
#   * `balances.last_updated_datetime` is the *entire* source clock for a realtime
#     cash balance. Plaid documents it as appearing "only when the institution is
#     `ins_128026`", so §8.1's fallback is `fetched_at` and every other account sits
#     at `UNKNOWN` under I5. Whether Sandbox supplies it decides how much of §8.1's
#     realtime branch task `12` can ever exercise before Production.
#   * `institution_price_datetime` is *preferred over* `institution_price_as_of` for
#     holdings, and is likewise documented as select-institution only. §8.1 also warns
#     that it "may contain default time values (such as 00:00:00)", so its presence
#     and type is what tells task `14` whether it is reading an instant or a date.
#
# Observing only the `*_as_of` pair would have reported a complete source clock while
# the two fields §8.1 reaches for *first* went unmeasured. (Codex caught that omission
# in the PR #49 pre-execution review, 2026-09-07.)
ACCOUNT_FIELDS = (
    "account_id",
    "name",
    "type",
    "subtype",
    "balances.current",
    "balances.available",
    "balances.limit",
    "balances.iso_currency_code",
    "balances.unofficial_currency_code",
    "balances.last_updated_datetime",
)
HOLDING_FIELDS = (
    "account_id",
    "security_id",
    "quantity",
    "institution_price",
    "institution_value",
    "cost_basis",
    "iso_currency_code",
    "institution_price_as_of",
    "institution_price_datetime",
)
SECURITY_FIELDS = (
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
    """The rehearsal could not proceed, with a reason that carries no secret."""


class RehearsalClient(Protocol):
    """The four calls this rehearsal needs, as :class:`PlaidClient` implements them.

    Narrow on purpose, in both directions: task ``05``'s fakes do not grow methods for
    Link, and this module cannot reach an endpoint it did not declare.
    """

    def first_institution_supporting(
        self, *, products: Sequence[str], country_codes: Sequence[str]
    ) -> str: ...

    def sandbox_public_token_create(
        self, *, institution_id: str, products: Sequence[str], username: str, password: str
    ) -> str: ...

    def item_public_token_exchange(self, public_token: str) -> ExchangedItem: ...

    def accounts_balance_get(self, access_token: str, *, fields: Sequence[str]) -> RecordSet: ...

    def investments_holdings_get(
        self, access_token: str, *, holding_fields: Sequence[str], security_fields: Sequence[str]
    ) -> HoldingsObservation: ...


@dataclass(frozen=True, slots=True)
class RehearsalOutcome:
    """What one full Link -> exchange -> fetch cycle established.

    ``institution_id``, ``item_id`` and ``secret_ref`` are carried because the run is
    not reconstructable without them, and **redacted from the repr** because this
    object is what a traceback renders and what a report is tempted to print. The first
    two name one of the owner's institutions (``AGENTS.md`` rule 0) and the third is a
    handle into the ``TokenStore``; none of them belongs in a transcript that gets
    pasted into a PR.
    """

    environment: PlaidEnvironment
    institution_id: str
    item_id: str
    secret_ref: str
    database: Path
    accounts: RecordSet
    holdings: RecordSet
    securities: RecordSet

    def __repr__(self) -> str:
        return (
            f"RehearsalOutcome(environment={self.environment.value!r}, "
            "institution_id=<redacted>, item_id=<redacted>, secret_ref=<redacted>, "
            f"database={str(self.database)!r}, accounts={self.accounts.count}, "
            f"holdings={self.holdings.count}, securities={self.securities.count})"
        )


class SandboxRehearsal:
    """One Link -> exchange -> fetch cycle, against Sandbox and nowhere else."""

    def __init__(
        self,
        credentials: PlaidCredentials,
        *,
        token_store: TokenStore,
        database: Path,
        client: RehearsalClient | None = None,
    ) -> None:
        if credentials.environment is not PlaidEnvironment.SANDBOX:
            # Not an assertion and not a caller's responsibility. The task's one
            # "must not" is *touch Production*, and F2a makes the failure permanent:
            # a successful Link spends a lifetime slot whether or not anyone meant
            # it. Refusing in the constructor means no code path exists in which a
            # Production credential reaches `/sandbox/public_token/create` — and it
            # happens before any client is built, so nothing is even configured with
            # the master credential.
            raise RehearsalError(
                "the rehearsal refuses to run against "
                f"{credentials.environment.value!r}: it exists so that Production is "
                "never the place a Link flow is tried out (task 06, F2a)"
            )
        self._credentials = credentials
        self._token_store = token_store
        self._database = database
        self._client: RehearsalClient = client if client is not None else PlaidClient(credentials)

    def exchange(self, public_token: str) -> tuple[ExchangedItem, str]:
        """Exchange for an ``access_token`` and write it down before anything else.

        §14a's ordering, rehearsed rather than described: the ``access_token`` is
        durable through :class:`TokenStore` *before* the caller records an item, so a
        crash in between leaves an orphan record and never a stranded slot. In Sandbox
        a stranded slot costs nothing, which is precisely why the ordering should be
        exercised here rather than first attempted in Production.
        """
        item = self._client.item_public_token_exchange(public_token)
        secret_ref = self._token_store.put(
            SecretKind.ACCESS_TOKEN,
            new_flow_id(),
            item.access_token,
            item_id=item.item_id,
        )
        return item, secret_ref

    def run(self) -> RehearsalOutcome:
        """The whole cycle, in the order Production will run it."""
        institution_id = self._client.first_institution_supporting(
            products=REQUIRED_PRODUCTS, country_codes=COUNTRY_CODES
        )
        public_token = self._client.sandbox_public_token_create(
            institution_id=institution_id,
            products=REQUIRED_PRODUCTS,
            username=SANDBOX_USERNAME,
            password=SANDBOX_PASSWORD,
        )
        item, secret_ref = self.exchange(public_token)
        accounts = self._client.accounts_balance_get(item.access_token, fields=ACCOUNT_FIELDS)
        investments = self._client.investments_holdings_get(
            item.access_token,
            holding_fields=HOLDING_FIELDS,
            security_fields=SECURITY_FIELDS,
        )
        return RehearsalOutcome(
            environment=self._credentials.environment,
            institution_id=institution_id,
            item_id=item.item_id,
            secret_ref=secret_ref,
            database=self._database,
            accounts=accounts,
            holdings=investments.holdings,
            securities=investments.securities,
        )
