"""Manual assets: a property's revision log and a share count times a quote.

``DESIGN.md`` section 12 answers O4 with **a revision log**, and the half of
that answer which is load-bearing is not "keep the old numbers" but *when each
one applies*: **a revision applies from its own date forward, and the curve
behind it does not move.** So a valuation is never a bare amount here — it is an
amount paired with the date it speaks for, which is what lets a reader ask for
the value *as of a day* rather than the value *now*.

The two kinds are asymmetric on purpose (section 8.1 R3):

- ``REAL_PROPERTY`` is ``MANUAL_STATIC``. Its clock is the owner's own
  valuation date, it never advances by itself, and it is *never stale* — it is
  doing exactly what it was configured to do.
- ``EQUITY_SHARES`` is ``MANUAL_QTY_LIVE_PRICE``. The *quantity* is manual and
  does not expire; the *price* obeys normal freshness rules, and its clock is
  the quote's own timestamp — never the moment we asked for it (R1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import StrEnum

from networth.model.figure import (
    SourcedFigure,
    require_minor_units,
    require_nonempty,
    require_utc,
)

#: ``source_clock`` for a property observation: the owner's own valuation date.
#: A distinct name from the quote clock because section 7 wants every freshness
#: claim traceable to the field that justified it, and these two are justified
#: by different fields belonging to different people.
MANUAL_VALUED_AS_OF = "MANUAL_VALUED_AS_OF"

#: ``source_clock`` for a share-count observation: the quote's own timestamp.
QUOTE_AS_OF = "QUOTE_AS_OF"

# Section 10.6 is single-currency (USD), and the schema carries a currency so
# that mixed units fail loudly rather than silently summing unlike things. This
# table is that loudness: converting a price into minor units needs to know how
# many there are per unit, and guessing 100 for an unknown code would turn a
# currency mistake into a wrong number instead of an error.
_MINOR_UNITS_PER_UNIT = {"USD": Decimal(100)}


class ManualAssetKind(StrEnum):
    """``manual_asset.kind``: the two shapes the schema's CHECK allows."""

    REAL_PROPERTY = "REAL_PROPERTY"
    EQUITY_SHARES = "EQUITY_SHARES"


def normalize_symbol(raw: str) -> str:
    """The one spelling of a ticker this program compares.

    A ticker is case-insensitive at every venue that quotes it, but a string
    comparison is not: a symbol stored as ``aapl`` and a quote keyed ``AAPL``
    would fail the "is this quote for this holding?" guard below and report a
    missing price for an asset that has one. Both sides normalize here, so the
    guard compares the *asset* rather than the spelling.
    """
    require_nonempty(raw.strip(), field="symbol")
    return raw.strip().upper()


def require_symbol(value: str, *, field: str) -> None:
    """Refuse a symbol that has not been through :func:`normalize_symbol`."""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if not value or value != value.strip().upper():
        raise ValueError(f"{field} must be a non-empty normalized ticker")


def parse_share_count(raw: str) -> Decimal:
    """``manual_asset.share_count`` is TEXT, and this is why.

    A share count is fractional (vesting, splits, DRIP) and money is never a
    float (``AGENTS.md``), so it is stored as text and read as a ``Decimal``.
    Text also means the column can hold things that are not quantities:
    ``Decimal`` happily parses ``"NaN"`` and ``"Infinity"``, and either would
    propagate into a total as a number nobody could interpret. Both are refused
    here, at the boundary, rather than found later in an amount.
    """
    if not isinstance(raw, str):
        raise TypeError("share_count must be text")
    try:
        shares = Decimal(raw.strip())
    except InvalidOperation:
        raise ValueError("share_count is not a decimal number") from None
    if not shares.is_finite():
        raise ValueError("share_count must be a finite quantity")
    if shares < 0:
        raise ValueError("share_count must not be negative")
    return shares


def to_minor_units(amount: Decimal, *, currency: str) -> int:
    """Convert a decimal amount in major units into integer minor units.

    Rounds half to even, which is the tie rule that does not accumulate a drift
    in one direction across many conversions. The result is an ``int`` because
    every amount that reaches storage is one (section 7).
    """
    if not isinstance(amount, Decimal):
        raise TypeError("amount must be a Decimal")
    if not amount.is_finite():
        raise ValueError("amount must be finite")
    scale = _MINOR_UNITS_PER_UNIT.get(currency)
    if scale is None:
        raise ValueError(f"no minor-unit scale is defined for currency {currency!r}")
    return int((amount * scale).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True, slots=True)
class Quote:
    """One price with the timestamp that price is true of.

    ``as_of`` is required and **is** the source clock (section 8.1's table). A
    quote that arrived without a usable timestamp is not a quote with a missing
    field, it is a price of unknown age — and substituting the moment we asked
    would be R1 violated at the one seam where a stale price is the failure
    being hunted (section 5).
    """

    symbol: str
    price: Decimal
    currency: str
    as_of: datetime

    def __post_init__(self) -> None:
        require_symbol(self.symbol, field="symbol")
        if not isinstance(self.price, Decimal):
            raise TypeError("price must be a Decimal; money is never a float")
        if not self.price.is_finite():
            raise ValueError("price must be finite")
        if self.price < 0:
            raise ValueError("price must not be negative")
        require_nonempty(self.currency, field="currency")
        require_utc(self.as_of, field="as_of")


@dataclass(frozen=True, slots=True)
class PropertyValuation:
    """One entry in a property's revision log: an amount and the day it speaks for.

    ``valued_as_of`` is the owner's date for this valuation, not the moment he
    typed it in. The two differ whenever a revision is entered late — an
    appraisal from June recorded in September applies from June — and section 12
    is explicit that the value used for a given day is the latest revision *as
    of that day*, which only means anything if the entry carries its own date.
    """

    value_minor: int
    currency: str
    valued_as_of: datetime

    def __post_init__(self) -> None:
        require_minor_units(self.value_minor, field="value_minor")
        require_nonempty(self.currency, field="currency")
        require_utc(self.valued_as_of, field="valued_as_of")

    @property
    def figure(self) -> SourcedFigure:
        """The amount inseparable from the clock that justifies it."""
        return SourcedFigure(
            value_minor=self.value_minor,
            currency=self.currency,
            as_of=self.valued_as_of,
            source_clock=MANUAL_VALUED_AS_OF,
        )


@dataclass(frozen=True, slots=True)
class EquityHolding:
    """``share_count`` shares of ``symbol``, last confirmed on ``set_on``.

    The quantity does not expire, but vesting changes it, which is why
    ``set_on`` travels with it: section 12 shows the holding as "N shares, set
    on <date>" and task 27 nudges the owner to re-confirm. A share count that
    drifts silently is this product's own failure mode arriving from the manual
    side, so the date is part of the value rather than a detail beside it.
    """

    symbol: str
    shares: Decimal
    currency: str
    set_on: datetime

    def __post_init__(self) -> None:
        require_symbol(self.symbol, field="symbol")
        if not isinstance(self.shares, Decimal):
            raise TypeError("shares must be a Decimal")
        if not self.shares.is_finite() or self.shares < 0:
            raise ValueError("shares must be a finite non-negative quantity")
        require_nonempty(self.currency, field="currency")
        require_utc(self.set_on, field="set_on")

    def value_with(self, quote: Quote) -> SourcedFigure:
        """``shares × price``, dated by the quote and by nothing else.

        Both guards below refuse rather than compute. A quote for another symbol
        is the failure mode of a batched request whose keys got crossed, and a
        quote in another currency is section 10.6's "unlike things": either one
        produces a number that looks like money and is not this holding's.
        """
        if not isinstance(quote, Quote):
            raise TypeError("quote must be a Quote")
        if quote.symbol != self.symbol:
            raise ValueError(f"quote is for {quote.symbol!r}, not for {self.symbol!r}")
        if quote.currency != self.currency:
            raise ValueError(
                f"quote is priced in {quote.currency!r} but the account holds {self.currency!r}"
            )
        return SourcedFigure(
            value_minor=to_minor_units(self.shares * quote.price, currency=self.currency),
            currency=self.currency,
            as_of=quote.as_of,
            source_clock=QUOTE_AS_OF,
        )


@dataclass(frozen=True, slots=True)
class ManualAsset:
    """The ``manual_asset`` row: which kind, and the kind's own configuration.

    Exactly one of the two payloads is present, mirroring the schema's CHECK.
    Keeping them in one type rather than two lets a caller read the row without
    first knowing what it will find, and the ``__post_init__`` below is the
    reason a caller can then trust the payload it did find.
    """

    account_id: int
    kind: ManualAssetKind
    valuation: PropertyValuation | None = None
    holding: EquityHolding | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, int) or isinstance(self.account_id, bool):
            raise TypeError("account_id must be an integer")
        if self.account_id <= 0:
            raise ValueError("account_id must be positive")
        if not isinstance(self.kind, ManualAssetKind):
            raise TypeError("kind must be a ManualAssetKind")
        if self.kind is ManualAssetKind.REAL_PROPERTY:
            if self.valuation is None or self.holding is not None:
                raise ValueError("a REAL_PROPERTY asset carries a valuation and no holding")
        elif self.holding is None or self.valuation is not None:
            raise ValueError("an EQUITY_SHARES asset carries a holding and no valuation")
