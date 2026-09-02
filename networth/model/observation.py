"""Append-only account observation records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from networth.model.figure import SourcedFigure, require_nonempty, require_utc


class ObservationSource(StrEnum):
    PLAID_HOLDINGS = "PLAID_HOLDINGS"
    PLAID_BALANCE = "PLAID_BALANCE"
    MANUAL = "MANUAL"
    QUOTE = "QUOTE"


@dataclass(frozen=True, slots=True)
class ObservationDraft:
    """An observation before SQLite assigns its append-only row id."""

    sync_run_id: str
    account_id: int
    observed_at: datetime
    figure: SourcedFigure
    source: ObservationSource
    fetched_at: datetime
    is_carried_forward: bool

    def __post_init__(self) -> None:
        require_nonempty(self.sync_run_id, field="sync_run_id")
        if not isinstance(self.account_id, int) or isinstance(self.account_id, bool):
            raise TypeError("account_id must be an integer")
        if self.account_id <= 0:
            raise ValueError("account_id must be positive")
        require_utc(self.observed_at, field="observed_at")
        if not isinstance(self.figure, SourcedFigure):
            raise TypeError("figure must be a SourcedFigure")
        if not isinstance(self.source, ObservationSource):
            raise TypeError("source must be an ObservationSource")
        require_utc(self.fetched_at, field="fetched_at")
        if not isinstance(self.is_carried_forward, bool):
            raise TypeError("is_carried_forward must be a bool")

        clock_is_unknown = self.figure.source_clock == "UNKNOWN"
        if clock_is_unknown != (self.figure.as_of is None):
            raise ValueError(
                "observation source_clock must be UNKNOWN exactly when as_of is absent"
            )


@dataclass(frozen=True, slots=True)
class Observation(ObservationDraft):
    """A stored observation, including its immutable SQLite identity."""

    id: int

    def __post_init__(self) -> None:
        ObservationDraft.__post_init__(self)
        if not isinstance(self.id, int) or isinstance(self.id, bool):
            raise TypeError("id must be an integer")
        if self.id <= 0:
            raise ValueError("id must be positive")

    def as_draft(self) -> ObservationDraft:
        return ObservationDraft(
            sync_run_id=self.sync_run_id,
            account_id=self.account_id,
            observed_at=self.observed_at,
            figure=self.figure,
            source=self.source,
            fetched_at=self.fetched_at,
            is_carried_forward=self.is_carried_forward,
        )
