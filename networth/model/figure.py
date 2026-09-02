"""Money and age types shared by the SQLite repositories.

The value and its clock deliberately live in one object.  Returning an integer
and asking a caller to remember a second lookup for its age is the failure mode
this project exists to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

_CURRENCY = re.compile(r"[A-Z]{3}\Z")


def require_utc(value: datetime, *, field: str) -> None:
    """Reject naive or non-UTC timestamps at the domain boundary."""

    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")


def require_minor_units(value: int, *, field: str) -> None:
    """Money is an integer and ``bool`` is not accepted as one."""

    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field} must be integer minor units")


def require_nonnegative_int(value: int, *, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field} must be an integer")
    if value < 0:
        raise ValueError(f"{field} must not be negative")


def require_nonempty(value: str, *, field: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{field} must be non-empty with no surrounding whitespace")


class SnapshotAgeState(StrEnum):
    """The tagged age states stored with every snapshot total."""

    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    STATIC_ONLY = "STATIC_ONLY"


class AggregateSourceClock(StrEnum):
    """Evidence exposed with a figure read from a snapshot.

    ``snapshot`` stores one tagged age for all three aggregate figures rather
    than a literal ``source_clock`` column.  The repository still returns a
    figure-with-clock: this enum names the section-8.1 rule that produced the
    tag instead of fabricating an account-level clock.
    """

    OLDEST_CONTRIBUTING_SOURCE_AS_OF = "OLDEST_CONTRIBUTING_SOURCE_AS_OF"
    UNKNOWN = "UNKNOWN"
    STATIC_ONLY = "STATIC_ONLY"


@dataclass(frozen=True, slots=True)
class SourcedFigure:
    """Integer money inseparable from its age and source-clock evidence."""

    value_minor: int
    currency: str
    as_of: datetime | None
    source_clock: str

    def __post_init__(self) -> None:
        require_minor_units(self.value_minor, field="value_minor")
        if not isinstance(self.currency, str) or _CURRENCY.fullmatch(self.currency) is None:
            raise ValueError("currency must be a three-letter uppercase code")
        require_nonempty(self.source_clock, field="source_clock")
        if self.as_of is not None:
            require_utc(self.as_of, field="as_of")

        undated_clocks = {
            AggregateSourceClock.UNKNOWN.value,
            AggregateSourceClock.STATIC_ONLY.value,
        }
        if (self.as_of is None) != (self.source_clock in undated_clocks):
            raise ValueError("as_of is absent exactly for UNKNOWN or STATIC_ONLY source clocks")


@dataclass(frozen=True, slots=True)
class SnapshotAge:
    """The sum type represented by ``snapshot.age_state`` and its clocks."""

    state: SnapshotAgeState
    as_of: datetime | None
    oldest_known_source_as_of: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.state, SnapshotAgeState):
            raise TypeError("state must be a SnapshotAgeState")
        if self.as_of is not None:
            require_utc(self.as_of, field="as_of")
        if self.oldest_known_source_as_of is not None:
            require_utc(
                self.oldest_known_source_as_of,
                field="oldest_known_source_as_of",
            )

        if self.state is SnapshotAgeState.KNOWN:
            if self.as_of is None:
                raise ValueError("KNOWN snapshot age requires as_of")
            if self.oldest_known_source_as_of != self.as_of:
                raise ValueError(
                    "KNOWN snapshot age requires oldest_known_source_as_of to equal as_of"
                )
        elif self.as_of is not None:
            raise ValueError(f"{self.state.value} snapshot age cannot carry as_of")

        if (
            self.state is SnapshotAgeState.STATIC_ONLY
            and self.oldest_known_source_as_of is not None
        ):
            raise ValueError("STATIC_ONLY snapshot age has no advancing source-clock basis")

    @property
    def source_clock(self) -> AggregateSourceClock:
        if self.state is SnapshotAgeState.KNOWN:
            return AggregateSourceClock.OLDEST_CONTRIBUTING_SOURCE_AS_OF
        if self.state is SnapshotAgeState.UNKNOWN:
            return AggregateSourceClock.UNKNOWN
        return AggregateSourceClock.STATIC_ONLY

    def figure(self, value_minor: int, *, currency: str = "USD") -> SourcedFigure:
        """Bind an aggregate amount to this age; no bare snapshot read exists."""

        return SourcedFigure(
            value_minor=value_minor,
            currency=currency,
            as_of=self.as_of,
            source_clock=self.source_clock.value,
        )
