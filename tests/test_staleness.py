"""Task 11: source-clock freshness stays independent from successful calls."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from networth.model import (
    DisplayState,
    FreshnessAssessment,
    FreshnessPolicy,
    FreshnessState,
    ItemHealth,
    ItemState,
    ObservationDraft,
    ObservationSource,
    SourcedFigure,
)
from networth.staleness import (
    CASH_FRESH_FOR,
    FROZEN_MARKET_DAYS,
    HOLDINGS_POSTING_GRACE,
    StalenessMachine,
    UsEquityMarketCalendar,
    _regular_holidays,
)

MONDAY_CLOSE = datetime(2026, 1, 5, 21, 0, tzinfo=UTC)


def observation(
    source_as_of: datetime | None,
    *,
    fetched_at: datetime,
    carried_forward: bool = False,
    source: ObservationSource = ObservationSource.PLAID_HOLDINGS,
) -> ObservationDraft:
    return ObservationDraft(
        sync_run_id="synthetic-run",
        account_id=1,
        observed_at=fetched_at,
        figure=SourcedFigure(
            value_minor=123_45,
            currency="USD",
            as_of=source_as_of,
            source_clock="UNKNOWN" if source_as_of is None else "SYNTHETIC_SOURCE_CLOCK",
        ),
        source=source,
        fetched_at=fetched_at,
        is_carried_forward=carried_forward,
    )


def item(
    status: ItemState = ItemState.HEALTHY,
    *,
    status_since: datetime = MONDAY_CLOSE - timedelta(days=30),
) -> ItemHealth:
    return ItemHealth(
        id=1,
        plaid_item_id="synthetic-item",
        secret_ref="synthetic-secret-ref",
        status=status,
        status_since=status_since,
        last_polled_at=None,
        investments_last_successful_update=None,
        last_error_code=None,
        last_error_detail=None,
    )


def test_market_calendar_knows_weekends_holidays_dst_and_half_days() -> None:
    calendar = UsEquityMarketCalendar()

    assert calendar.session_close(date(2026, 1, 15)) == datetime(2026, 1, 15, 21, 0, tzinfo=UTC)
    assert calendar.session_close(date(2026, 7, 2)) == datetime(2026, 7, 2, 20, 0, tzinfo=UTC)
    assert calendar.session_close(date(2026, 1, 17)) is None  # Saturday

    for holiday in (
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    ):
        assert calendar.session_close(holiday) is None

    assert calendar.session_close(date(2026, 11, 27)) == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    assert calendar.session_close(date(2026, 12, 24)) == datetime(2026, 12, 24, 18, 0, tzinfo=UTC)
    assert calendar.session_close(date(2025, 7, 3)) == datetime(2025, 7, 3, 17, 0, tzinfo=UTC)


def test_calendar_pins_deliberate_new_year_and_juneteenth_boundaries() -> None:
    calendar = UsEquityMarketCalendar()

    # NYSE does not observe a Saturday New Year's Day on the preceding Friday.
    # The generator's year-keyed invariant matters even though session_close asks
    # for the session's year rather than the nominal holiday's year.
    assert date(2027, 12, 31) not in _regular_holidays(2028)
    assert calendar.session_close(date(2027, 12, 31)) == datetime(2027, 12, 31, 21, 0, tzinfo=UTC)

    # Juneteenth became an NYSE holiday in 2022, not retroactively in 2021.
    assert calendar.session_close(date(2021, 6, 18)) == datetime(2021, 6, 18, 20, 0, tzinfo=UTC)


def test_exceptional_exchange_closures_are_explicit_calendar_inputs() -> None:
    closed = date(2026, 8, 12)
    early = date(2026, 8, 13)
    calendar = UsEquityMarketCalendar(
        extra_holidays=[closed],
        extra_early_closes=[early],
    )

    assert calendar.session_close(closed) is None
    assert calendar.session_close(early) == datetime(2026, 8, 13, 17, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="both closed and an early close"):
        UsEquityMarketCalendar(extra_holidays=[closed], extra_early_closes=[closed])


def test_weekend_and_holiday_do_not_invent_a_new_market_close() -> None:
    machine = StalenessMachine()
    thursday_close = datetime(2026, 7, 2, 20, 0, tzinfo=UTC)
    row = observation(
        thursday_close,
        fetched_at=datetime(2026, 7, 5, 18, 0, tzinfo=UTC),
        source=ObservationSource.QUOTE,
    )

    # Friday July 3 is the observed Independence Day holiday, followed by a
    # weekend. Thursday therefore remains the latest close through Sunday.
    result = machine.assess(
        row,
        policy=FreshnessPolicy.MANUAL_QTY_LIVE_PRICE,
        item=None,
        at=datetime(2026, 7, 5, 18, 0, tzinfo=UTC),
    )

    assert result.state is FreshnessState.FRESH


def test_half_day_changes_the_clock_at_its_real_close_not_four_pm() -> None:
    machine = StalenessMachine()
    wednesday_close = datetime(2026, 11, 25, 21, 0, tzinfo=UTC)
    row = observation(
        wednesday_close,
        fetched_at=datetime(2026, 11, 27, 18, 0, tzinfo=UTC),
        source=ObservationSource.QUOTE,
    )

    before_early_close = machine.assess(
        row,
        policy=FreshnessPolicy.MANUAL_QTY_LIVE_PRICE,
        item=None,
        at=datetime(2026, 11, 27, 17, 59, 59, tzinfo=UTC),
    )
    at_early_close = machine.assess(
        row,
        policy=FreshnessPolicy.MANUAL_QTY_LIVE_PRICE,
        item=None,
        at=datetime(2026, 11, 27, 18, 0, tzinfo=UTC),
    )

    assert before_early_close.state is FreshnessState.FRESH
    assert at_early_close.state is FreshnessState.STALE


def test_holdings_wait_through_the_posting_grace_after_a_new_close() -> None:
    machine = StalenessMachine()
    friday_close = datetime(2026, 1, 9, 21, 0, tzinfo=UTC)
    monday_close = datetime(2026, 1, 12, 21, 0, tzinfo=UTC)
    row = observation(friday_close, fetched_at=monday_close)

    still_in_grace = machine.assess(
        row,
        policy=FreshnessPolicy.SYNCED_HOLDINGS,
        item=item(),
        at=monday_close + HOLDINGS_POSTING_GRACE - timedelta(microseconds=1),
    )
    grace_elapsed = machine.assess(
        row,
        policy=FreshnessPolicy.SYNCED_HOLDINGS,
        item=item(),
        at=monday_close + HOLDINGS_POSTING_GRACE,
    )

    assert still_in_grace.state is FreshnessState.FRESH
    assert grace_elapsed.state is FreshnessState.STALE


def test_owner_thresholds_match_the_literal_design_values() -> None:
    assert timedelta(hours=36) == CASH_FRESH_FOR
    assert timedelta(hours=12) == HOLDINGS_POSTING_GRACE


def test_a_successful_new_call_cannot_refresh_a_frozen_source_clock() -> None:
    machine = StalenessMachine()
    fifth_market_day_due = datetime(2026, 1, 12, 21, 0, tzinfo=UTC) + HOLDINGS_POSTING_GRACE
    # The call succeeded now, but the source date is still the old Monday close.
    newly_fetched = observation(MONDAY_CLOSE, fetched_at=fifth_market_day_due)

    result = machine.assess(
        newly_fetched,
        policy=FreshnessPolicy.SYNCED_HOLDINGS,
        item=item(),
        at=fifth_market_day_due,
    )

    assert newly_fetched.fetched_at == fifth_market_day_due
    assert result.source_as_of == MONDAY_CLOSE
    assert result.market_days_without_advance == FROZEN_MARKET_DAYS
    assert result.state is FreshnessState.FROZEN
    assert result.frozen_alert_required is True
    assert machine.display_state([result]) is DisplayState.ACTION_NEEDED


def test_four_market_days_is_waiting_and_the_fifth_is_action_needed() -> None:
    machine = StalenessMachine()
    monday_close = datetime(2026, 1, 12, 21, 0, tzinfo=UTC)
    fifth_close = datetime(2026, 1, 20, 21, 0, tzinfo=UTC)
    row = observation(monday_close, fetched_at=fifth_close)
    source_item = item()

    before_fifth_window_ends = machine.assess(
        row,
        policy=FreshnessPolicy.SYNCED_HOLDINGS,
        item=source_item,
        at=fifth_close + HOLDINGS_POSTING_GRACE - timedelta(microseconds=1),
    )
    fifth_window_ended = machine.assess(
        row,
        policy=FreshnessPolicy.SYNCED_HOLDINGS,
        item=source_item,
        at=fifth_close + HOLDINGS_POSTING_GRACE,
    )

    # Jan 13-16 are four sessions. Jan 20 is the fifth because Jan 19 is MLK Day.
    assert before_fifth_window_ends.market_days_without_advance == FROZEN_MARKET_DAYS - 1
    assert before_fifth_window_ends.state is FreshnessState.STALE
    assert machine.display_state([before_fifth_window_ends]) is DisplayState.WAITING
    assert fifth_window_ended.market_days_without_advance == FROZEN_MARKET_DAYS
    assert fifth_window_ended.state is FreshnessState.FROZEN


def test_stale_before_the_fifth_market_day_is_waiting_not_an_alert() -> None:
    machine = StalenessMachine()
    four_days_due = datetime(2026, 1, 9, 21, 0, tzinfo=UTC) + HOLDINGS_POSTING_GRACE
    row = observation(MONDAY_CLOSE, fetched_at=four_days_due)

    result = machine.assess(
        row,
        policy=FreshnessPolicy.SYNCED_HOLDINGS,
        item=item(),
        at=four_days_due,
    )

    assert result.market_days_without_advance == FROZEN_MARKET_DAYS - 1
    assert result.state is FreshnessState.STALE
    assert result.frozen_alert_required is False
    assert machine.display_state([result]) is DisplayState.WAITING


def test_transient_degraded_polls_cannot_suppress_a_frozen_source_forever() -> None:
    machine = StalenessMachine()
    status = ItemState.HEALTHY
    status_since = MONDAY_CLOSE - timedelta(days=30)
    frozen_while_healthy = False

    for elapsed_days in range(1, 88):
        at = MONDAY_CLOSE + timedelta(days=elapsed_days, hours=12)
        next_status = ItemState.DEGRADED if elapsed_days % 3 == 0 else ItemState.HEALTHY
        if next_status is not status:
            status = next_status
            status_since = at

        result = machine.assess(
            observation(MONDAY_CLOSE, fetched_at=at),
            policy=FreshnessPolicy.SYNCED_HOLDINGS,
            item=item(status, status_since=status_since),
            at=at,
        )
        if status is ItemState.DEGRADED:
            assert result.market_days_without_advance == 0
            assert result.frozen_alert_required is False
        else:
            frozen_while_healthy |= result.frozen_alert_required

    assert frozen_while_healthy is True


@pytest.mark.parametrize("status", [ItemState.DEGRADED, ItemState.NEEDS_REAUTH, ItemState.REVOKED])
def test_an_unhealthy_item_never_turns_axis_b_into_frozen(status: ItemState) -> None:
    machine = StalenessMachine()
    at = datetime(2026, 2, 2, 21, 0, tzinfo=UTC) + HOLDINGS_POSTING_GRACE
    result = machine.assess(
        observation(MONDAY_CLOSE, fetched_at=at),
        policy=FreshnessPolicy.SYNCED_HOLDINGS,
        item=item(status),
        at=at,
    )

    assert result.state is FreshnessState.STALE
    assert result.market_days_without_advance == 0
    expected = DisplayState.WAITING if status is ItemState.DEGRADED else DisplayState.ACTION_NEEDED
    assert machine.display_state([result]) is expected


def test_unknown_never_renders_fresh_or_escalates_even_after_a_year() -> None:
    machine = StalenessMachine()
    at = datetime(2027, 1, 15, 21, 0, tzinfo=UTC)
    result = machine.assess(
        observation(None, fetched_at=at),
        policy=FreshnessPolicy.SYNCED_BALANCE,
        item=item(),
        at=at,
    )

    assert result.state is FreshnessState.UNKNOWN
    assert result.is_fresh is False
    assert result.market_days_without_advance == 0
    assert result.frozen_alert_required is False
    assert machine.display_state([result]) is DisplayState.WAITING


def test_a_carried_forward_observation_keeps_aging_from_the_inherited_clock() -> None:
    machine = StalenessMachine()
    at = datetime(2026, 1, 12, 21, 0, tzinfo=UTC) + HOLDINGS_POSTING_GRACE
    direct = observation(MONDAY_CLOSE, fetched_at=MONDAY_CLOSE)
    carried = observation(MONDAY_CLOSE, fetched_at=at, carried_forward=True)

    direct_result = machine.assess(
        direct,
        policy=FreshnessPolicy.SYNCED_HOLDINGS,
        item=item(),
        at=at,
    )
    carried_result = machine.assess(
        carried,
        policy=FreshnessPolicy.SYNCED_HOLDINGS,
        item=item(),
        at=at,
    )

    assert direct_result.state is FreshnessState.FROZEN
    assert carried_result.state is direct_result.state
    assert carried_result.market_days_without_advance == direct_result.market_days_without_advance
    assert carried_result.is_carried_forward is True


def test_cash_uses_its_wall_clock_window_and_not_the_market_close() -> None:
    machine = StalenessMachine()
    source_as_of = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
    row = observation(
        source_as_of,
        fetched_at=source_as_of,
        source=ObservationSource.PLAID_BALANCE,
    )

    at_boundary = machine.assess(
        row,
        policy=FreshnessPolicy.SYNCED_BALANCE,
        item=item(),
        at=source_as_of + CASH_FRESH_FOR,
    )
    beyond_boundary = machine.assess(
        row,
        policy=FreshnessPolicy.SYNCED_BALANCE,
        item=item(),
        at=source_as_of + CASH_FRESH_FOR + timedelta(microseconds=1),
    )

    assert at_boundary.state is FreshnessState.FRESH
    assert beyond_boundary.state is FreshnessState.STALE


def test_static_manual_value_is_healthy_by_policy_but_not_called_fresh() -> None:
    machine = StalenessMachine()
    at = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    result = machine.assess(
        observation(
            datetime(2020, 1, 1, tzinfo=UTC),
            fetched_at=at,
            source=ObservationSource.MANUAL,
        ),
        policy=FreshnessPolicy.MANUAL_STATIC,
        item=None,
        at=at,
    )

    assert result.state is FreshnessState.STATIC
    assert result.is_fresh is False
    assert machine.display_state([result]) is DisplayState.OK


def test_display_precedence_keeps_action_waiting_and_ok_distinct() -> None:
    machine = StalenessMachine()
    at = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    static = machine.assess(
        observation(
            datetime(2020, 1, 1, tzinfo=UTC),
            fetched_at=at,
            source=ObservationSource.MANUAL,
        ),
        policy=FreshnessPolicy.MANUAL_STATIC,
        item=None,
        at=at,
    )

    assert machine.display_state([static]) is DisplayState.OK
    assert machine.display_state([static], item_states=[ItemState.DEGRADED]) is DisplayState.WAITING
    assert (
        machine.display_state([static], item_states=[ItemState.NEEDS_REAUTH])
        is DisplayState.ACTION_NEEDED
    )
    assert (
        machine.display_state([static], unreconciled_account_count=1) is DisplayState.ACTION_NEEDED
    )


def test_policy_item_pairing_and_utc_are_checked_at_the_boundary() -> None:
    machine = StalenessMachine()
    at = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    row = observation(datetime(2026, 1, 14, 21, 0, tzinfo=UTC), fetched_at=at)

    with pytest.raises(ValueError, match="requires an Item"):
        machine.assess(row, policy=FreshnessPolicy.SYNCED_HOLDINGS, item=None, at=at)
    with pytest.raises(ValueError, match="cannot have an Item"):
        machine.assess(row, policy=FreshnessPolicy.MANUAL_QTY_LIVE_PRICE, item=item(), at=at)
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        machine.assess(
            row,
            policy=FreshnessPolicy.SYNCED_HOLDINGS,
            item=item(),
            at=at.astimezone(timezone(timedelta(hours=-5))),
        )


def test_static_manual_value_requires_its_valuation_date() -> None:
    machine = StalenessMachine()
    at = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="valued_as_of"):
        machine.assess(
            observation(None, fetched_at=at, source=ObservationSource.MANUAL),
            policy=FreshnessPolicy.MANUAL_STATIC,
            item=None,
            at=at,
        )


def test_a_frozen_assessment_cannot_disagree_with_the_shared_threshold() -> None:
    with pytest.raises(ValueError, match="FROZEN requires"):
        FreshnessAssessment(
            state=FreshnessState.FROZEN,
            source_as_of=MONDAY_CLOSE,
            market_days_without_advance=FROZEN_MARKET_DAYS - 1,
            is_carried_forward=False,
            item_state=ItemState.HEALTHY,
        )
    with pytest.raises(ValueError, match="condition must also be labelled FROZEN"):
        FreshnessAssessment(
            state=FreshnessState.STALE,
            source_as_of=MONDAY_CLOSE,
            market_days_without_advance=FROZEN_MARKET_DAYS,
            is_carried_forward=False,
            item_state=ItemState.HEALTHY,
        )
