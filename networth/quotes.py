"""``QuoteClient``: a price together with the timestamp that price is true of.

``DESIGN.md`` section 12 values an ``EQUITY_SHARES`` account as
``share_count × quote(symbol)``, "reusing the working quotes integration from
the sibling project" — the same vendor, endpoint and payload shape that project
already runs against the owner's key. The daemon reads its own copy of that key
from ``/etc/networth/quotes.env`` (section 15), installed by the owner like
every other runtime secret; it never reads the Mac's copy (``AGENTS.md`` rule 1).

The one thing this module exists to get right is in section 8.1's table: for a
manual quantity times a live price, **the source clock is the quote's own
timestamp**, never the moment we asked. A price of unknown age is therefore not
a quote with a missing field — it is refused here, at the boundary, because the
alternative is a number that looks current and is not, which is the exact
failure this product was built to catch.

Two consequences worth stating, because both were nearly written the easy way:

- **Prices are parsed as ``Decimal``, not ``float``.** ``json.loads`` produces
  floats by default, and ``AGENTS.md`` says money is never one. The parse hook
  below is not a stylistic preference — it is the difference between a share
  count times a price and a share count times something near it.
- **Absence is absence.** A symbol the venue did not answer for is missing from
  the result rather than present as zero, and every failure below raises instead
  of substituting. Task 11 decides what a stale or missing price does to the
  net-worth total; this seam's only job is to be honest about what it got.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

import urllib3

from networth.config import SECRETS_DIR, ConfigError, read_env_file
from networth.model.manual import Quote, normalize_symbol

#: The owner-installed file, named in section 15's inventory.
QUOTES_FILE_NAME = "quotes.env"

#: The two keys inside it. They are the names the sibling project's
#: ``alpaca.env`` already uses, so the owner installs a copy of a file he holds
#: rather than transcribing one into a new shape.
KEY_ID_FIELD = "ALPACA_KEY_ID"
SECRET_KEY_FIELD = "ALPACA_SECRET_KEY"

#: Optional. Which market-data feed to ask for; absent means "whatever this
#: account's entitlement gives by default". Deliberately not defaulted in code:
#: feeds differ in coverage *and* in price, and the owner's standing rule is
#: that nothing spends beyond what he already subscribes to. Configuring it is
#: his decision, not a constant an agent picked.
FEED_FIELD = "ALPACA_FEED"

QUOTES_URL = "https://data.alpaca.markets/v2/stocks/quotes/latest"

#: This endpoint quotes US equities, which are priced in USD. It is a fact
#: about the venue rather than a default for the account: the currency travels
#: with the quote so that :meth:`~networth.model.manual.EquityHolding.value_with`
#: can refuse a price in units the account does not hold (section 10.6).
QUOTE_CURRENCY = "USD"

DEFAULT_TIMEOUT_SECONDS = 8.0


class QuoteUnavailable(RuntimeError):
    """No usable price for this symbol, for any reason.

    One type covers transport failure, a refusing venue and a malformed or
    undated payload, because the caller's decision is the same in all of them:
    it does not have a price it may use. The message never carries the response
    body or the request headers — the headers are the credential.
    """


@dataclass(frozen=True, slots=True)
class QuoteCredentials:
    """The quotes key pair, read from the owner's file.

    Both fields are rendered as redacted, for the reason ``PlaidCredentials``
    gives: the vendor authenticates with the *pair*, so half of it in a
    traceback is half of a working credential. Section 12 notes this key is
    read-only market data of no personal significance — which lowers the
    consequence of a leak, not the standard for one.
    """

    key_id: str
    secret_key: str

    def __repr__(self) -> str:
        return "QuoteCredentials(key_id=<redacted>, secret_key=<redacted>)"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
        }


def load_quote_credentials(*, secrets_dir: Path = SECRETS_DIR) -> QuoteCredentials:
    """Read ``/etc/networth/quotes.env``, or refuse with the reason.

    ``secrets_dir`` exists so tests can point at a temporary directory. It is
    not a fallback to another host's directory, and there is none: this daemon
    reads its own copy only (section 15, ``AGENTS.md`` rule 1).
    """
    path = secrets_dir / QUOTES_FILE_NAME
    values = read_env_file(path, describe="quotes credentials")
    missing = [key for key in (KEY_ID_FIELD, SECRET_KEY_FIELD) if not values.get(key)]
    if missing:
        raise ConfigError(f"{path} has no usable {', '.join(missing)}")
    return QuoteCredentials(key_id=values[KEY_ID_FIELD], secret_key=values[SECRET_KEY_FIELD])


def configured_feed(*, secrets_dir: Path = SECRETS_DIR) -> str | None:
    """The optional ``ALPACA_FEED`` setting, or ``None`` if it is not set."""

    path = secrets_dir / QUOTES_FILE_NAME
    feed = read_env_file(path, describe="quotes credentials").get(FEED_FIELD, "").strip()
    return feed or None


class _Response(Protocol):
    # Read-only on purpose: `urllib3`'s `data` is a property, and a protocol
    # that declared a plain attribute here would exclude the real transport
    # while still accepting every fake in the test suite.
    @property
    def status(self) -> int: ...

    @property
    def data(self) -> bytes: ...


class _Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        fields: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> _Response: ...


class _Urllib3Transport:
    """The default transport, kept behind the same seam as the test fakes.

    ``urllib3`` is already a direct dependency (it is what the Plaid SDK speaks),
    so this adds nothing to the install. It is a named adapter rather than the
    pool passed directly because ``PoolManager.request`` takes its timeout
    through ``**kwargs``: handing it over as a ``_Transport`` would type-check
    only by widening the seam to ``Any``, and then a future signature change
    would surface as a runtime ``TypeError`` on the sync host instead of an
    error here.
    """

    def __init__(self) -> None:
        self._pool = urllib3.PoolManager()

    def request(
        self,
        method: str,
        url: str,
        *,
        fields: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> _Response:
        return self._pool.request(
            method,
            url,
            fields=dict(fields),
            headers=dict(headers),
            timeout=timeout,
        )


class QuoteClient:
    """One HTTP call, one price per symbol, each dated by the venue.

    The transport is injected so that every test below runs without a network
    and without a key: the behaviour worth pinning here is what this code does
    with a payload, and reaching the real venue to find out would make the test
    suite depend on a market being open.
    """

    def __init__(
        self,
        credentials: QuoteCredentials,
        *,
        transport: _Transport | None = None,
        feed: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._credentials = credentials
        self._transport: _Transport = _Urllib3Transport() if transport is None else transport
        self._feed = feed
        self._timeout = timeout

    def get_quote(self, symbol: str) -> Quote:
        """The price for one symbol, or :class:`QuoteUnavailable`.

        A venue that answers without this symbol is a refusal, not an empty
        success: the caller asked what a holding is worth, and "no answer" must
        not be able to read as anything else.
        """
        wanted = normalize_symbol(symbol)
        quotes = self.get_quotes([wanted])
        quote = quotes.get(wanted)
        if quote is None:
            raise QuoteUnavailable(f"no quote for {wanted}")
        return quote

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        """Prices for the symbols the venue answered for, keyed by symbol.

        Symbols the venue skipped are **absent** from the mapping, so a caller
        that compares what it asked for against what it got can see which
        holdings it has no price for. One request covers the batch because the
        endpoint is a batch endpoint; asking per symbol would multiply the
        rate-limit cost of a daily sweep for nothing.
        """
        wanted = [normalize_symbol(symbol) for symbol in symbols]
        if not wanted:
            return {}

        fields = {"symbols": ",".join(wanted)}
        if self._feed is not None:
            fields["feed"] = self._feed

        try:
            response = self._transport.request(
                "GET",
                QUOTES_URL,
                fields=fields,
                headers=self._credentials.headers,
                timeout=self._timeout,
            )
        except Exception as exc:
            # The type name only. A transport exception can quote the request it
            # failed on, and this request is authenticated by its headers.
            raise QuoteUnavailable(f"the quotes request failed: {type(exc).__name__}") from None

        if response.status != 200:
            raise QuoteUnavailable(f"the quotes venue answered {response.status}")

        entries = _payload_quotes(response.data)
        found: dict[str, Quote] = {}
        for raw_symbol, entry in entries.items():
            if not isinstance(raw_symbol, str) or not raw_symbol.strip():
                continue
            symbol = normalize_symbol(raw_symbol)
            if symbol not in wanted:
                # A price for something we did not ask about cannot answer a
                # question about something we did — the same guard the Item
                # poller applies to a response about another Item.
                continue
            quote = _quote_from_entry(symbol, entry)
            if quote is not None:
                found[symbol] = quote
        return found


def _payload_quotes(body: bytes) -> dict[Any, Any]:
    """The ``quotes`` object, or a refusal. ``parse_float=Decimal`` is the point."""

    try:
        decoded = json.loads(body, parse_float=Decimal)
    except (ValueError, UnicodeDecodeError):
        raise QuoteUnavailable(
            "the quotes venue answered with something that is not JSON"
        ) from None
    if not isinstance(decoded, dict):
        raise QuoteUnavailable("the quotes payload is not an object")
    entries = decoded.get("quotes")
    if entries is None:
        raise QuoteUnavailable("the quotes payload carries no quotes")
    if not isinstance(entries, dict):
        raise QuoteUnavailable("the quotes payload's quotes are not an object")
    return entries


def _quote_from_entry(symbol: str, entry: Any) -> Quote | None:
    """One payload entry as a :class:`Quote`, or ``None`` if it is not one.

    ``None`` rather than an exception: one unusable entry in a batch must not
    cost the prices of the other holdings, and its symbol is simply missing from
    the result, which is how the caller learns it has no price.
    """
    if not isinstance(entry, dict):
        return None
    price = _midpoint(entry.get("bp"), entry.get("ap"))
    as_of = _as_of(entry.get("t"))
    if price is None or as_of is None:
        return None
    return Quote(symbol=symbol, price=price, currency=QUOTE_CURRENCY, as_of=as_of)


def _midpoint(bid: Any, ask: Any) -> Decimal | None:
    """Halfway between bid and ask, in exact decimal arithmetic.

    The midpoint rather than either side: a bid systematically undervalues a
    holding and an ask systematically overvalues it, and the sibling project
    settled on the same mark against this same endpoint. Non-positive sides are
    refused because the venue uses them for "no current quote".
    """
    sides: list[Decimal] = []
    for side in (bid, ask):
        if isinstance(side, bool) or not isinstance(side, (Decimal, int)):
            return None
        try:
            value = Decimal(side)
        except InvalidOperation:
            return None
        if not value.is_finite() or value <= 0:
            return None
        sides.append(value)
    return (sides[0] + sides[1]) / 2


def _as_of(stamp: Any) -> datetime | None:
    """The venue's own timestamp for this price, normalized to UTC.

    A naive timestamp is refused rather than assumed to be UTC. The assumption
    would be right most of the time and silently wrong the rest, and what it
    would corrupt is the field the whole staleness machine reads.
    """
    if not isinstance(stamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "FEED_FIELD",
    "KEY_ID_FIELD",
    "QUOTES_FILE_NAME",
    "QUOTES_URL",
    "QUOTE_CURRENCY",
    "SECRET_KEY_FIELD",
    "QuoteClient",
    "QuoteCredentials",
    "QuoteUnavailable",
    "configured_feed",
    "load_quote_credentials",
]
