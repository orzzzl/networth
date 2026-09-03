"""Connection-health records for Plaid Items.

Axis A is an Item state; the investments update timestamp is source-clock
evidence for Axis B.  They travel in one record so callers cannot mistake a
successful poll for fresh holdings data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from networth.model.figure import require_nonempty, require_utc


class ItemState(StrEnum):
    """Axis A of DESIGN.md section 8.2; data age is not on this axis."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    NEEDS_REAUTH = "NEEDS_REAUTH"
    REVOKED = "REVOKED"

    @property
    def owner_actionable(self) -> bool:
        """Whether this state asks the owner to act."""

        return self in (ItemState.NEEDS_REAUTH, ItemState.REVOKED)


@dataclass(frozen=True, slots=True)
class ItemHealth:
    """The durable health and source-clock facts for one Item."""

    id: int
    plaid_item_id: str
    secret_ref: str
    status: ItemState
    status_since: datetime
    last_polled_at: datetime | None
    investments_last_successful_update: datetime | None
    last_error_code: str | None
    last_error_detail: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.id, int) or isinstance(self.id, bool):
            raise TypeError("id must be an integer")
        if self.id <= 0:
            raise ValueError("id must be positive")
        require_nonempty(self.plaid_item_id, field="plaid_item_id")
        require_nonempty(self.secret_ref, field="secret_ref")
        if not isinstance(self.status, ItemState):
            raise TypeError("status must be an ItemState")
        require_utc(self.status_since, field="status_since")
        if self.last_polled_at is not None:
            require_utc(self.last_polled_at, field="last_polled_at")
        if self.investments_last_successful_update is not None:
            require_utc(
                self.investments_last_successful_update,
                field="investments_last_successful_update",
            )
        for field, value in (
            ("last_error_code", self.last_error_code),
            ("last_error_detail", self.last_error_detail),
        ):
            if value is not None:
                require_nonempty(value, field=field)
        if self.status is ItemState.HEALTHY and (
            self.last_error_code is not None or self.last_error_detail is not None
        ):
            raise ValueError("a HEALTHY Item cannot retain an error")


@dataclass(frozen=True, slots=True)
class ItemHealthUpdate:
    """One `/item/get` observation before it is applied to an Item.

    ``status is None`` is the explicit no-transition result used for
    ``PENDING_DISCONNECT``.  It advances the poll clock but preserves the
    current state and error, because section 8.2 does not put that warning on
    Axis A.

    ``investments_status_observed`` distinguishes a response that carried a
    literal null update clock from an HTTP or transport failure that supplied
    no investments status at all.  The former clears the clock to UNKNOWN; the
    latter leaves the last observed source-clock evidence in place to keep
    ageing.
    """

    polled_at: datetime
    status: ItemState | None
    error_code: str | None
    error_detail: str | None
    investments_status_observed: bool
    investments_last_successful_update: datetime | None

    def __post_init__(self) -> None:
        require_utc(self.polled_at, field="polled_at")
        if self.status is not None and not isinstance(self.status, ItemState):
            raise TypeError("status must be an ItemState or None")
        if not isinstance(self.investments_status_observed, bool):
            raise TypeError("investments_status_observed must be a bool")
        if self.investments_last_successful_update is not None:
            require_utc(
                self.investments_last_successful_update,
                field="investments_last_successful_update",
            )
        if (
            not self.investments_status_observed
            and self.investments_last_successful_update is not None
        ):
            raise ValueError("an unobserved investments status cannot carry an update clock")

        for field, value in (
            ("error_code", self.error_code),
            ("error_detail", self.error_detail),
        ):
            if value is not None:
                require_nonempty(value, field=field)

        if self.status in (None, ItemState.HEALTHY):
            if self.error_code is not None or self.error_detail is not None:
                raise ValueError("HEALTHY and no-transition updates cannot carry an error")
        elif self.error_detail is None:
            raise ValueError("an error-state update requires safe error detail")
