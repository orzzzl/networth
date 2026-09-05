"""Evaluate source clocks without ever promoting a successful fetch into freshness.

The market calendar is deliberately local and dependency-free: task 11 may not
add a package, while weekends, exchange holidays, DST and 13:00 early closes
are correctness inputs rather than presentation details.  Exceptional exchange
closures can be injected explicitly without changing the ordinary NYSE rules.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from zoneinfo import ZoneInfo

from networth.model import ItemHealth, ItemState, ObservationDraft
from networth.model.figure import require_nonnegative_int, require_utc
from networth.model.staleness import (
    FROZEN_MARKET_DAYS,
    DisplayState,
    FreshnessAssessment,
    FreshnessPolicy,
    FreshnessState,
)

CASH_FRESH_FOR = timedelta(hours=36)
HOLDINGS_POSTING_GRACE = timedelta(hours=12)

_EASTERN = ZoneInfo("America/New_York")
_REGULAR_CLOSE = time(16, 0)
_EARLY_CLOSE = time(13, 0)


def _require_date(value: date, *, field: str) -> None:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError(f"{field} must be a date")


def _require_nonnegative_duration(value: timedelta, *, field: str) -> None:
    if not isinstance(value, timedelta):
        raise TypeError(f"{field} must be a timedelta")
    if value < timedelta(0):
        raise ValueError(f"{field} must not be negative")


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed_fixed(day: date, *, observe_saturday: bool = True) -> date | None:
    if day.weekday() == 5:
        return day - timedelta(days=1) if observe_saturday else None
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _easter_sunday(year: int) -> date:
    """Gregorian Easter via the anonymous algorithm, sufficient for Good Friday."""

    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = (h + ell - 7 * m + 114) % 31 + 1
    return date(year, month, day)


@cache
def _regular_holidays(year: int) -> frozenset[date]:
    holidays = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed_fixed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed_fixed(date(year, 12, 25)),
    }
    # NYSE does not move a Saturday New Year's Day to the preceding Friday.
    holidays.add(_observed_fixed(date(year, 1, 1), observe_saturday=False))
    if year >= 2022:
        holidays.add(_observed_fixed(date(year, 6, 19)))
    return frozenset(day for day in holidays if day is not None)


@cache
def _regular_early_closes(year: int) -> frozenset[date]:
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    candidates = {
        date(year, 7, 3),
        thanksgiving + timedelta(days=1),
        date(year, 12, 24),
    }
    holidays = _regular_holidays(year)
    return frozenset(day for day in candidates if day.weekday() < 5 and day not in holidays)


class UsEquityMarketCalendar:
    """NYSE core-session closes, returned as aware UTC instants.

    The recurring rules cover the schedule named by the design.  Constructor
    overrides are the explicit seam for one-off exchange closures or early
    closes; silently pretending an exceptional closure was a normal session
    would advance the staleness clock on a day when no new close existed.
    """

    __slots__ = ("_extra_early_closes", "_extra_holidays")

    def __init__(
        self,
        *,
        extra_holidays: Iterable[date] = (),
        extra_early_closes: Iterable[date] = (),
    ) -> None:
        holidays = frozenset(extra_holidays)
        early_closes = frozenset(extra_early_closes)
        for value in holidays:
            _require_date(value, field="extra_holidays entry")
        for value in early_closes:
            _require_date(value, field="extra_early_closes entry")
        if holidays & early_closes:
            raise ValueError("a market date cannot be both closed and an early close")
        self._extra_holidays = holidays
        self._extra_early_closes = early_closes

    def session_close(self, session_date: date) -> datetime | None:
        """The session close on ``session_date``, or ``None`` when closed."""

        _require_date(session_date, field="session_date")
        if (
            session_date.weekday() >= 5
            or session_date in _regular_holidays(session_date.year)
            or session_date in self._extra_holidays
        ):
            return None
        close_time = (
            _EARLY_CLOSE
            if session_date in _regular_early_closes(session_date.year)
            or session_date in self._extra_early_closes
            else _REGULAR_CLOSE
        )
        return datetime.combine(session_date, close_time, tzinfo=_EASTERN).astimezone(UTC)

    def latest_completed_close(
        self,
        at: datetime,
        *,
        grace: timedelta = timedelta(0),
    ) -> datetime:
        """Newest close whose posting grace has also elapsed by ``at``."""

        require_utc(at, field="at")
        _require_nonnegative_duration(grace, field="grace")
        candidate = at.astimezone(_EASTERN).date()
        while True:
            close = self.session_close(candidate)
            if close is not None and close + grace <= at:
                return close
            candidate -= timedelta(days=1)

    def completed_closes_after(
        self,
        after: datetime,
        through: datetime,
        *,
        grace: timedelta = timedelta(0),
    ) -> tuple[datetime, ...]:
        """Session closes after ``after`` whose posting windows ended by ``through``."""

        require_utc(after, field="after")
        require_utc(through, field="through")
        _require_nonnegative_duration(grace, field="grace")
        if through <= after:
            return ()

        first_day = after.astimezone(_EASTERN).date()
        last_day = through.astimezone(_EASTERN).date()
        closes: list[datetime] = []
        candidate = first_day
        while candidate <= last_day:
            close = self.session_close(candidate)
            if close is not None and close > after and close + grace <= through:
                closes.append(close)
            candidate += timedelta(days=1)
        return tuple(closes)


class StalenessMachine:
    """Evaluate Axis B and compose it with Axis A only at the display boundary."""

    __slots__ = ("_calendar",)

    def __init__(self, calendar: UsEquityMarketCalendar | None = None) -> None:
        self._calendar = UsEquityMarketCalendar() if calendar is None else calendar
        if not isinstance(self._calendar, UsEquityMarketCalendar):
            raise TypeError("calendar must be a UsEquityMarketCalendar")

    def assess(
        self,
        observation: ObservationDraft,
        *,
        policy: FreshnessPolicy,
        item: ItemHealth | None,
        at: datetime,
    ) -> FreshnessAssessment:
        """Classify one source clock; ``fetched_at`` never substitutes for it.

        Task 12 owns raw-provider interpretation.  In particular, it converts a
        date-granular holdings value (including Plaid's documented midnight
        default) to this calendar's close while it still knows which upstream
        field supplied the value.  A timestamp alone cannot safely distinguish
        that default from a genuinely precise midnight instant.
        """

        if not isinstance(observation, ObservationDraft):
            raise TypeError("observation must be an ObservationDraft")
        if not isinstance(policy, FreshnessPolicy):
            raise TypeError("policy must be a FreshnessPolicy")
        if item is not None and not isinstance(item, ItemHealth):
            raise TypeError("item must be an ItemHealth or None")
        require_utc(at, field="at")
        if policy.requires_item != (item is not None):
            owner = "requires" if policy.requires_item else "cannot have"
            raise ValueError(f"{policy.value} {owner} an Item health record")

        source_as_of = observation.figure.as_of
        if policy is FreshnessPolicy.MANUAL_STATIC:
            if source_as_of is None:
                raise ValueError("MANUAL_STATIC requires its owner-supplied valued_as_of")
            return FreshnessAssessment(
                state=FreshnessState.STATIC,
                source_as_of=source_as_of,
                market_days_without_advance=0,
                is_carried_forward=observation.is_carried_forward,
                item_state=None,
            )

        if source_as_of is None:
            return FreshnessAssessment(
                state=FreshnessState.UNKNOWN,
                source_as_of=None,
                market_days_without_advance=0,
                is_carried_forward=observation.is_carried_forward,
                item_state=None if item is None else item.status,
            )

        grace = self._posting_grace(policy)
        is_fresh = self._inside_expectation(policy, source_as_of, at, grace)
        market_days = self._healthy_market_days_without_advance(
            source_as_of,
            item,
            at,
            grace=grace,
        )
        if is_fresh:
            state = FreshnessState.FRESH
        elif market_days >= FROZEN_MARKET_DAYS:
            state = FreshnessState.FROZEN
        else:
            state = FreshnessState.STALE
        return FreshnessAssessment(
            state=state,
            source_as_of=source_as_of,
            market_days_without_advance=market_days,
            is_carried_forward=observation.is_carried_forward,
            item_state=None if item is None else item.status,
        )

    def display_state(
        self,
        assessments: Iterable[FreshnessAssessment],
        *,
        item_states: Iterable[ItemState] = (),
        unreconciled_account_count: int = 0,
    ) -> DisplayState:
        """Compose the two axes with the precedence in DESIGN section 9.2."""

        collected = tuple(assessments)
        for assessment in collected:
            if not isinstance(assessment, FreshnessAssessment):
                raise TypeError("assessments must contain FreshnessAssessment records")
        explicit_items = tuple(item_states)
        for state in explicit_items:
            if not isinstance(state, ItemState):
                raise TypeError("item_states must contain ItemState values")
        require_nonnegative_int(
            unreconciled_account_count,
            field="unreconciled_account_count",
        )

        all_item_states = explicit_items + tuple(
            assessment.item_state for assessment in collected if assessment.item_state is not None
        )
        if (
            unreconciled_account_count > 0
            or any(state.owner_actionable for state in all_item_states)
            or any(assessment.state is FreshnessState.FROZEN for assessment in collected)
        ):
            return DisplayState.ACTION_NEEDED
        if any(state is ItemState.DEGRADED for state in all_item_states) or any(
            assessment.state in (FreshnessState.STALE, FreshnessState.UNKNOWN)
            for assessment in collected
        ):
            return DisplayState.WAITING
        return DisplayState.OK

    def _inside_expectation(
        self,
        policy: FreshnessPolicy,
        source_as_of: datetime,
        at: datetime,
        grace: timedelta,
    ) -> bool:
        if policy is FreshnessPolicy.SYNCED_BALANCE:
            return at <= source_as_of + CASH_FRESH_FOR
        required_close = self._calendar.latest_completed_close(at, grace=grace)
        return source_as_of >= required_close

    def _healthy_market_days_without_advance(
        self,
        source_as_of: datetime,
        item: ItemHealth | None,
        at: datetime,
        *,
        grace: timedelta,
    ) -> int:
        if item is None or item.status is not ItemState.HEALTHY:
            return 0
        # A transient Axis A failure must not erase Axis B's source-clock evidence.
        # ``status_since`` records only the latest transition, so anchoring here would
        # let recurring DEGRADED blips suppress a frozen-data alert forever.  Recovery
        # gates evaluation again; only an advancing source clock resets its age.
        return len(
            self._calendar.completed_closes_after(
                source_as_of,
                at,
                grace=grace,
            )
        )

    @staticmethod
    def _posting_grace(policy: FreshnessPolicy) -> timedelta:
        if policy is FreshnessPolicy.SYNCED_HOLDINGS:
            return HOLDINGS_POSTING_GRACE
        return timedelta(0)


__all__ = [
    "CASH_FRESH_FOR",
    "FROZEN_MARKET_DAYS",
    "HOLDINGS_POSTING_GRACE",
    "StalenessMachine",
    "UsEquityMarketCalendar",
]
