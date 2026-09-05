"""The property revision log, read the way section 12 requires it to be read.

Section 12 answers O4 with a revision log and then names the one thing that is
easy to get wrong: **the value used for a given day is the latest revision *as
of that day*, never the latest revision outright.** A log that always answers
with its newest entry is a mutable settings field with extra rows — it redraws
history every time an estimate changes, which is the same class of lie as a
frozen balance rendered as live, running the other way.

Mechanically a revision needs nothing new (section 12): observations are already
append-only (section 7), so a revision is a new observation carrying its own
``source_as_of``, and this module never issues an ``UPDATE``. What it adds is the
*ordering*, which is not the same as insertion order:

- entries are ordered by the date the owner says the valuation speaks for
  (``source_as_of``), **not** by when he typed it in (``observed_at``). An
  appraisal dated June and entered in September applies from June forward.
- among entries carrying the same date, the one entered later wins: re-entering
  a date is how a typo is corrected, and a correction that lost to the row it
  corrects would be unfixable without an ``UPDATE``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from networth.model import (
    MANUAL_VALUED_AS_OF,
    ObservationDraft,
    ObservationSource,
    PropertyValuation,
)
from networth.model.figure import require_utc
from networth.model.observation import Observation


class NotARevisionError(ValueError):
    """An observation was offered to the log that is not a property revision."""


@dataclass(frozen=True, slots=True)
class Revision:
    """One entry, with the two clocks section 8.1 insists on keeping apart.

    ``valuation.valued_as_of`` is what the amount is true of; ``entered_at`` is
    when it reached us. Only the first orders the log, but the second decides
    ties, so both have to survive the trip out of storage.
    """

    valuation: PropertyValuation
    entered_at: datetime
    sequence: int

    def __post_init__(self) -> None:
        if not isinstance(self.valuation, PropertyValuation):
            raise TypeError("valuation must be a PropertyValuation")
        require_utc(self.entered_at, field="entered_at")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool):
            raise TypeError("sequence must be an integer")

    @property
    def order_key(self) -> tuple[datetime, datetime, int]:
        """Date-spoken-for first; entry time only breaks a tie between equals."""

        return (self.valuation.valued_as_of, self.entered_at, self.sequence)


class PropertyRevisionLog:
    """Every revision recorded for one property, answerable as of a day."""

    __slots__ = ("_entries",)

    def __init__(self, entries: Iterable[Revision]) -> None:
        collected = tuple(entries)
        for entry in collected:
            if not isinstance(entry, Revision):
                raise TypeError("entries must be Revision records")
        self._entries = tuple(sorted(collected, key=lambda entry: entry.order_key))

    @classmethod
    def from_observations(cls, observations: Iterable[Observation]) -> PropertyRevisionLog:
        """Build the log from an account's stored history.

        Anything that is not a manual valuation is refused rather than skipped.
        A quote or a Plaid balance appearing on a property account means the
        account was mis-classified upstream, and silently dropping it would let
        this log answer confidently while the caller's premise is wrong.
        """

        entries: list[Revision] = []
        for observation in observations:
            if not isinstance(observation, Observation):
                raise TypeError("observations must be Observation records")
            if (
                observation.source is not ObservationSource.MANUAL
                or observation.figure.source_clock != MANUAL_VALUED_AS_OF
            ):
                raise NotARevisionError(
                    f"observation {observation.id} is a "
                    f"{observation.source.value}/{observation.figure.source_clock} record, "
                    f"not a property revision"
                )
            if observation.figure.as_of is None:  # pragma: no cover - SourcedFigure forbids it
                raise NotARevisionError(f"observation {observation.id} carries no valuation date")
            entries.append(
                Revision(
                    valuation=PropertyValuation(
                        value_minor=observation.figure.value_minor,
                        currency=observation.figure.currency,
                        valued_as_of=observation.figure.as_of,
                    ),
                    entered_at=observation.observed_at,
                    sequence=observation.id,
                )
            )
        return cls(entries)

    @property
    def entries(self) -> tuple[Revision, ...]:
        """Every revision, oldest first by the date it speaks for."""

        return self._entries

    def as_of(self, day: datetime) -> Revision | None:
        """The revision in force on ``day``, or ``None`` before the first one.

        ``None`` is the honest answer for a day that precedes the log: the owner
        had not valued the property yet, and inventing the earliest revision for
        that period would apply a number backwards — the deformation section 12
        exists to forbid, arriving through the front door.
        """

        require_utc(day, field="day")
        in_force = [entry for entry in self._entries if entry.valuation.valued_as_of <= day]
        return in_force[-1] if in_force else None

    def current(self, *, now: datetime) -> Revision | None:
        """The revision in force today.

        A revision dated in the future is stored but not yet in force, so this
        is ``as_of(now)`` rather than ``entries[-1]``. Reading the newest row
        outright is the specific bug section 12 names.
        """

        return self.as_of(now)


def revision_draft(
    *,
    sync_run_id: str,
    account_id: int,
    valuation: PropertyValuation,
    observed_at: datetime,
) -> ObservationDraft:
    """A revision as what it is mechanically: one append-only observation.

    ``fetched_at`` is ``observed_at`` because nothing was fetched — the owner
    supplied the number, so the moment we learned it and the moment we recorded
    it are the same moment. The clock that ages the figure is neither of those:
    it is the owner's ``valued_as_of``, carried on the figure itself.
    """

    if not isinstance(valuation, PropertyValuation):
        raise TypeError("valuation must be a PropertyValuation")
    return ObservationDraft(
        sync_run_id=sync_run_id,
        account_id=account_id,
        observed_at=observed_at,
        figure=valuation.figure,
        source=ObservationSource.MANUAL,
        fetched_at=observed_at,
        is_carried_forward=False,
    )


__all__ = [
    "NotARevisionError",
    "PropertyRevisionLog",
    "Revision",
    "revision_draft",
]
