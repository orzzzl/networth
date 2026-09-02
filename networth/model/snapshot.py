"""Stored net-worth snapshots and their mandatory honesty annotations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from networth.model.figure import (
    SnapshotAge,
    SourcedFigure,
    require_nonempty,
    require_nonnegative_int,
    require_utc,
)


@dataclass(frozen=True, slots=True)
class SnapshotCounts:
    """All counts that qualify a stored total."""

    account_count: int
    stale_account_count: int
    unknown_freshness_account_count: int
    static_account_count: int
    reauth_account_count: int
    unreconciled_account_count: int

    def __post_init__(self) -> None:
        for name in (
            "account_count",
            "stale_account_count",
            "unknown_freshness_account_count",
            "static_account_count",
            "reauth_account_count",
            "unreconciled_account_count",
        ):
            require_nonnegative_int(getattr(self, name), field=name)


@dataclass(frozen=True, slots=True)
class SnapshotDraft:
    """A successful run's aggregate record before SQLite assigns its row id."""

    sync_run_id: str
    taken_at: datetime
    net_worth: SourcedFigure
    assets: SourcedFigure
    liabilities: SourcedFigure
    counts: SnapshotCounts
    is_complete: bool
    age: SnapshotAge

    def __post_init__(self) -> None:
        require_nonempty(self.sync_run_id, field="sync_run_id")
        require_utc(self.taken_at, field="taken_at")
        if not isinstance(self.counts, SnapshotCounts):
            raise TypeError("counts must be SnapshotCounts")
        if not isinstance(self.is_complete, bool):
            raise TypeError("is_complete must be a bool")
        if not isinstance(self.age, SnapshotAge):
            raise TypeError("age must be a SnapshotAge")

        for name, figure in (
            ("net_worth", self.net_worth),
            ("assets", self.assets),
            ("liabilities", self.liabilities),
        ):
            if not isinstance(figure, SourcedFigure):
                raise TypeError(f"{name} must be a SourcedFigure")
            if figure.currency != "USD":
                raise ValueError("snapshot figures must use the schema's single USD currency")
            if figure.as_of != self.age.as_of:
                raise ValueError(f"{name} as_of must match the snapshot age")
            if figure.source_clock != self.age.source_clock.value:
                raise ValueError(f"{name} source_clock must match the snapshot age rule")


@dataclass(frozen=True, slots=True)
class Snapshot(SnapshotDraft):
    """An immutable snapshot returned by the repository."""

    id: int

    def __post_init__(self) -> None:
        SnapshotDraft.__post_init__(self)
        if not isinstance(self.id, int) or isinstance(self.id, bool):
            raise TypeError("id must be an integer")
        if self.id <= 0:
            raise ValueError("id must be positive")

    def as_draft(self) -> SnapshotDraft:
        return SnapshotDraft(
            sync_run_id=self.sync_run_id,
            taken_at=self.taken_at,
            net_worth=self.net_worth,
            assets=self.assets,
            liabilities=self.liabilities,
            counts=self.counts,
            is_complete=self.is_complete,
            age=self.age,
        )
