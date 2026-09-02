"""Task 04 domain contracts: figures cannot shed their clocks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from networth.model import (
    SnapshotAge,
    SnapshotAgeState,
    SnapshotCounts,
    SnapshotDraft,
    SourcedFigure,
)

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
SOURCE_AS_OF = datetime(2026, 1, 14, 21, 0, tzinfo=UTC)


def test_money_is_integer_minor_units_not_float_or_bool() -> None:
    with pytest.raises(TypeError, match="integer minor units"):
        SourcedFigure(12.34, "USD", SOURCE_AS_OF, "SYNTHETIC_CLOCK")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="integer minor units"):
        SourcedFigure(True, "USD", SOURCE_AS_OF, "SYNTHETIC_CLOCK")


def test_figure_requires_an_explicit_currency_and_source_clock() -> None:
    with pytest.raises(ValueError, match="three-letter uppercase"):
        SourcedFigure(1234, "usd", SOURCE_AS_OF, "SYNTHETIC_CLOCK")
    with pytest.raises(ValueError, match="source_clock"):
        SourcedFigure(1234, "USD", SOURCE_AS_OF, "")


@pytest.mark.parametrize(
    ("as_of", "source_clock"),
    [(None, "SYNTHETIC_CLOCK"), (SOURCE_AS_OF, "UNKNOWN")],
)
def test_figure_cannot_separate_a_date_from_its_clock(
    as_of: datetime | None,
    source_clock: str,
) -> None:
    with pytest.raises(ValueError, match="as_of is absent exactly"):
        SourcedFigure(1234, "USD", as_of, source_clock)


def test_all_timestamps_must_be_aware_utc() -> None:
    naive = datetime(2026, 1, 15, 12, 0)
    non_utc = NOW.astimezone(timezone(timedelta(hours=-5)))

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        SourcedFigure(1234, "USD", naive, "SYNTHETIC_CLOCK")
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        SourcedFigure(1234, "USD", non_utc, "SYNTHETIC_CLOCK")


def test_snapshot_age_is_a_tagged_value() -> None:
    with pytest.raises(ValueError, match="requires as_of"):
        SnapshotAge(SnapshotAgeState.KNOWN, None, None)
    with pytest.raises(ValueError, match="cannot carry as_of"):
        SnapshotAge(SnapshotAgeState.UNKNOWN, SOURCE_AS_OF, SOURCE_AS_OF)
    with pytest.raises(ValueError, match="no advancing source-clock basis"):
        SnapshotAge(SnapshotAgeState.STATIC_ONLY, None, SOURCE_AS_OF)


def test_snapshot_figures_cannot_disagree_with_their_age() -> None:
    age = SnapshotAge(SnapshotAgeState.KNOWN, SOURCE_AS_OF, SOURCE_AS_OF)
    wrong_clock = SourcedFigure(1234, "USD", SOURCE_AS_OF, "DIFFERENT_CLOCK")

    with pytest.raises(ValueError, match="source_clock must match"):
        SnapshotDraft(
            sync_run_id="run-model",
            taken_at=NOW,
            net_worth=wrong_clock,
            assets=age.figure(1234),
            liabilities=age.figure(0),
            counts=SnapshotCounts(1, 0, 0, 0, 0, 0),
            is_complete=True,
            age=age,
        )


@pytest.mark.parametrize(
    "counts",
    [
        SnapshotCounts(1, 1, 0, 0, 0, 0),
        SnapshotCounts(1, 0, 0, 0, 0, 1),
    ],
)
def test_snapshot_with_stale_or_unreconciled_accounts_cannot_be_complete(
    counts: SnapshotCounts,
) -> None:
    age = SnapshotAge(SnapshotAgeState.KNOWN, SOURCE_AS_OF, SOURCE_AS_OF)

    with pytest.raises(ValueError, match="cannot be complete"):
        SnapshotDraft(
            sync_run_id="run-incomplete",
            taken_at=NOW,
            net_worth=age.figure(1234),
            assets=age.figure(1234),
            liabilities=age.figure(0),
            counts=counts,
            is_complete=True,
            age=age,
        )


def test_snapshot_category_counts_cannot_exceed_account_count() -> None:
    with pytest.raises(ValueError, match="stale_account_count must not exceed"):
        SnapshotCounts(1, 2, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("age", "unknown_freshness_account_count"),
    [
        (SnapshotAge(SnapshotAgeState.KNOWN, SOURCE_AS_OF, SOURCE_AS_OF), 1),
        (SnapshotAge(SnapshotAgeState.UNKNOWN, None, SOURCE_AS_OF), 0),
    ],
)
def test_unknown_age_and_contributing_unknown_count_cannot_disagree(
    age: SnapshotAge,
    unknown_freshness_account_count: int,
) -> None:
    with pytest.raises(ValueError, match="positive exactly when snapshot age is UNKNOWN"):
        SnapshotDraft(
            sync_run_id="run-age-disagreement",
            taken_at=NOW,
            net_worth=age.figure(1234),
            assets=age.figure(1234),
            liabilities=age.figure(0),
            counts=SnapshotCounts(1, 0, unknown_freshness_account_count, 0, 0, 0),
            is_complete=True,
            age=age,
        )


def test_unreconciled_noncontributor_does_not_make_known_age_unknown() -> None:
    age = SnapshotAge(SnapshotAgeState.KNOWN, SOURCE_AS_OF, SOURCE_AS_OF)

    draft = SnapshotDraft(
        sync_run_id="run-unreconciled",
        taken_at=NOW,
        net_worth=age.figure(1234),
        assets=age.figure(1234),
        liabilities=age.figure(0),
        counts=SnapshotCounts(2, 0, 0, 0, 0, 1),
        is_complete=False,
        age=age,
    )

    assert draft.age.state is SnapshotAgeState.KNOWN
