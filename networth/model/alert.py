"""The four alerts DESIGN section 11 kept, and the record that carries one.

Alerting has exactly one channel — the payload the phone fetches — so an alert
is a durable row first and a message second.  The types here encode three rules
that a later reader would otherwise have to re-derive from prose:

- **Which Axis A states alert is read from :class:`~networth.model.item.ItemState`
  rather than listed again.**  ``DEGRADED`` is deliberately not owner-actionable,
  and section 11 says it must never raise; deriving the mapping from
  :attr:`~networth.model.item.ItemState.owner_actionable` means the two can never
  drift apart.
- **A subject is an Item or an account, never both and never neither.**  The
  ``alert`` table allows both columns to be set; the alerting rules do not, and
  "one alert per item per state entry" is only a well-defined key once the
  subject is unambiguous.
- **A frozen-data alert cannot exist without the source clock it was raised
  for.**  Section 11 says that alert resolves only when ``source_as_of``
  advances, so the value it must advance *past* is part of the record rather
  than something the resolver re-derives later and might get wrong.

Publication overdue is **not** here.  It is section 11's own hole: the alert
reports the failure of the channel it would have to travel over, so a row
claiming to deliver it would be a lie.  The phone detects it independently as
task ``22``'s ``HOST_NOT_PUBLISHING``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from networth.model.figure import require_nonempty, require_utc
from networth.model.item import ItemState


class AlertKind(StrEnum):
    """The four kinds, and the only four this schema's vocabulary admits."""

    NEEDS_REAUTH = "NEEDS_REAUTH"
    REVOKED = "REVOKED"
    FROZEN_DATA = "FROZEN_DATA"
    PENDING_RECONCILIATION = "PENDING_RECONCILIATION"

    @property
    def is_item_scoped(self) -> bool:
        """Whether the subject is an Item rather than an account."""

        return self in (AlertKind.NEEDS_REAUTH, AlertKind.REVOKED)

    @property
    def carries_source_clock(self) -> bool:
        """Only frozen data resolves on an advancing clock, so only it stores one."""

        return self is AlertKind.FROZEN_DATA

    @staticmethod
    def for_item_state(state: ItemState) -> AlertKind | None:
        """The alert an Axis A state raises, or ``None`` when it raises nothing.

        The decision is :attr:`ItemState.owner_actionable`, not a second list of
        state names: section 11 forbids alerting on ``DEGRADED``, and section 9.2
        already decides which states ask the owner to act.  Two lists would be
        one refactor away from disagreeing, and the disagreement would be silent.
        """

        if not isinstance(state, ItemState):
            raise TypeError("state must be an ItemState")
        if not state.owner_actionable:
            return None
        return AlertKind(state.value)


@dataclass(frozen=True, slots=True)
class AlertDraft:
    """An alert about to be raised, before the database assigns it an id."""

    kind: AlertKind
    created_at: datetime
    message: str
    item_id: int | None = None
    account_id: int | None = None
    raised_source_as_of: datetime | None = None

    def __post_init__(self) -> None:
        _validate_alert_fields(
            kind=self.kind,
            created_at=self.created_at,
            message=self.message,
            item_id=self.item_id,
            account_id=self.account_id,
            raised_source_as_of=self.raised_source_as_of,
        )


@dataclass(frozen=True, slots=True)
class Alert:
    """A stored alert, including the three lifecycle clocks the schema carries.

    ``acknowledged_at`` is read but never written by v0, and that is a property
    of the architecture rather than an omission: acknowledging happens on the
    phone, and there is no phone-to-host channel for it to travel back over
    (section 9.3).  The column is surfaced so a reader can see it is always
    ``None``, instead of a write path being invented for a message that cannot
    arrive.
    """

    id: int
    kind: AlertKind
    created_at: datetime
    message: str
    item_id: int | None
    account_id: int | None
    raised_source_as_of: datetime | None
    notified_at: datetime | None
    acknowledged_at: datetime | None
    resolved_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.id, int) or isinstance(self.id, bool):
            raise TypeError("id must be an integer")
        if self.id <= 0:
            raise ValueError("id must be positive")
        _validate_alert_fields(
            kind=self.kind,
            created_at=self.created_at,
            message=self.message,
            item_id=self.item_id,
            account_id=self.account_id,
            raised_source_as_of=self.raised_source_as_of,
        )
        for field, value in (
            ("notified_at", self.notified_at),
            ("acknowledged_at", self.acknowledged_at),
            ("resolved_at", self.resolved_at),
        ):
            if value is None:
                continue
            require_utc(value, field=field)
            if value < self.created_at:
                raise ValueError(f"{field} cannot precede created_at")

    @property
    def is_open(self) -> bool:
        """Alerts persist until resolved; nothing else closes one."""

        return self.resolved_at is None

    @property
    def subject_id(self) -> int:
        """The Item id or account id this alert is about."""

        subject = self.item_id if self.kind.is_item_scoped else self.account_id
        if subject is None:  # pragma: no cover - forbidden by __post_init__
            raise ValueError("an alert always has a subject")
        return subject


def _validate_alert_fields(
    *,
    kind: AlertKind,
    created_at: datetime,
    message: str,
    item_id: int | None,
    account_id: int | None,
    raised_source_as_of: datetime | None,
) -> None:
    if not isinstance(kind, AlertKind):
        raise TypeError("kind must be an AlertKind")
    require_utc(created_at, field="created_at")
    require_nonempty(message, field="message")

    held, empty = ("item_id", "account_id") if kind.is_item_scoped else ("account_id", "item_id")
    subject = item_id if held == "item_id" else account_id
    other = account_id if held == "item_id" else item_id
    if not isinstance(subject, int) or isinstance(subject, bool):
        raise TypeError(f"{kind.value} must name an integer {held}")
    if subject <= 0:
        raise ValueError(f"{held} must be positive")
    if other is not None:
        raise ValueError(f"{kind.value} is scoped to {held}; it cannot also carry {empty}")

    if kind.carries_source_clock:
        if raised_source_as_of is None:
            raise ValueError(
                "FROZEN_DATA resolves only when source_as_of advances, so the clock it "
                "was raised for is required"
            )
        require_utc(raised_source_as_of, field="raised_source_as_of")
    elif raised_source_as_of is not None:
        raise ValueError(f"{kind.value} does not resolve on a source clock")


__all__ = ["Alert", "AlertDraft", "AlertKind"]
