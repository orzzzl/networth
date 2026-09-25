"""Full-sync catch-up uses committed successes, including across downtime."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from networth.scheduling import FullSyncSchedule, ScheduleStateError
from networth.staleness import UsEquityMarketCalendar
from networth.storage import migrate

FRIDAY_READY = datetime(2026, 9, 18, 21, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def _run(
    db: sqlite3.Connection,
    *,
    start: datetime = FRIDAY_READY,
    finish: datetime | None = FRIDAY_READY,
    ok: int | None = 1,
    kind: str = "FULL_SYNC",
) -> None:
    db.execute(
        'INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok, kind) '
        "VALUES (lower(hex(randomblob(16))), ?, ?, 'TEST', ?, ?)",
        (start.isoformat(), None if finish is None else finish.isoformat(), ok, kind),
    )


def test_empty_state_is_due_and_checking_never_consumes_the_job(db: sqlite3.Connection) -> None:
    planner = FullSyncSchedule(db)
    before = db.total_changes
    first = planner.due(at=FRIDAY_READY)
    assert first == planner.due(at=FRIDAY_READY)
    assert first.due and first.market_due and first.elapsed_due
    assert first.last_successful_start is None
    assert first.last_successful_finish is None
    assert first.checked_at == FRIDAY_READY
    assert first.market_ready_at == FRIDAY_READY
    assert db.total_changes == before
    assert not db.in_transaction


@pytest.mark.parametrize(
    ("kind", "ok", "finished"),
    [("OTHER", 1, True), ("FULL_SYNC", 0, True), ("FULL_SYNC", None, False)],
)
def test_manual_failed_and_interrupted_runs_do_not_satisfy_due(
    db: sqlite3.Connection, kind: str, ok: int | None, finished: bool
) -> None:
    _run(db, kind=kind, ok=ok, finish=FRIDAY_READY if finished else None)
    db.commit()
    status = FullSyncSchedule(db).due(at=FRIDAY_READY + timedelta(minutes=1))
    assert status.market_due and status.elapsed_due
    assert status.last_successful_finish is None


def test_failed_and_manual_runs_do_not_replace_an_older_success(db: sqlite3.Connection) -> None:
    _run(db)
    later = FRIDAY_READY + timedelta(hours=21)
    _run(db, start=later, finish=later, ok=0)
    _run(db, start=later, finish=later, kind="OTHER")
    db.commit()
    status = FullSyncSchedule(db).due(at=later)
    assert not status.market_due
    assert status.elapsed_due
    assert status.last_successful_finish == FRIDAY_READY


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(microseconds=1)])
def test_weekend_twenty_hour_boundary_is_strict(db: sqlite3.Connection, delta: timedelta) -> None:
    _run(db)
    db.commit()
    status = FullSyncSchedule(db).due(at=FRIDAY_READY + timedelta(hours=20) + delta)
    assert not status.market_due
    assert status.elapsed_due == bool(delta)
    assert status.due == bool(delta)


def test_successful_saturday_sync_resets_elapsed_clock_on_sunday(db: sqlite3.Connection) -> None:
    _run(db)
    saturday = FRIDAY_READY + timedelta(hours=21)
    _run(db, start=saturday, finish=saturday)
    db.commit()
    status = FullSyncSchedule(db).due(at=saturday + timedelta(hours=19))
    assert not status.due
    assert status.market_ready_at == FRIDAY_READY
    assert FullSyncSchedule(db).due(at=saturday + timedelta(hours=21)).elapsed_due


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 9, 21), datetime(2026, 9, 21, 21, tzinfo=UTC)),
        (date(2026, 11, 27), datetime(2026, 11, 27, 19, tzinfo=UTC)),
        (date(2026, 11, 30), datetime(2026, 11, 30, 22, tzinfo=UTC)),
    ],
)
def test_market_rule_uses_close_grace_dst_and_early_close(
    db: sqlite3.Connection, day: date, expected: datetime
) -> None:
    # Morning success is younger than 20h but must not cover tonight's close.
    morning = datetime(day.year, day.month, day.day, 14, tzinfo=UTC)
    _run(db, start=morning, finish=morning)
    db.commit()
    planner = FullSyncSchedule(db)
    assert not planner.due(at=expected - timedelta(microseconds=1)).due
    ready = planner.due(at=expected)
    assert ready.market_ready_at == expected
    assert ready.due and ready.market_due and not ready.elapsed_due
    _run(db, start=expected, finish=expected)
    db.commit()
    assert not planner.due(at=expected).due


def test_a_success_finished_after_close_does_not_prove_fetch_started_after_it(
    db: sqlite3.Connection,
) -> None:
    _run(db, start=FRIDAY_READY - timedelta(minutes=5), finish=FRIDAY_READY)
    db.commit()
    status = FullSyncSchedule(db).due(at=FRIDAY_READY)
    assert status.market_due
    assert not status.elapsed_due


def test_clocks_are_independent_when_successes_finish_out_of_order(db: sqlite3.Connection) -> None:
    _run(db, start=FRIDAY_READY, finish=FRIDAY_READY + timedelta(minutes=1))
    # An older, slower run finishes last; it must not erase the post-close run.
    late_finish = FRIDAY_READY + timedelta(minutes=5)
    _run(db, start=FRIDAY_READY - timedelta(minutes=5), finish=late_finish)
    db.commit()
    status = FullSyncSchedule(db).due(at=late_finish)
    assert not status.due
    assert status.last_successful_start == FRIDAY_READY
    assert status.last_successful_finish == late_finish


def test_fractional_timestamp_order_uses_instants_not_sqlite_text(db: sqlite3.Connection) -> None:
    _run(db)
    _run(db, start=FRIDAY_READY, finish=FRIDAY_READY)
    db.execute("UPDATE sync_run SET finished_at = '2026-09-18T21:00:00Z' WHERE rowid = 1")
    db.execute("UPDATE sync_run SET finished_at = '2026-09-18T21:00:00.500000Z' WHERE rowid = 2")
    db.commit()
    later = FRIDAY_READY + timedelta(microseconds=500000)
    status = FullSyncSchedule(db).due(at=later + timedelta(hours=20))
    assert status.last_successful_finish == later
    assert not status.due


def test_holiday_and_exceptional_closure_use_calendar_not_weekday_guess(
    db: sqlite3.Connection,
) -> None:
    holiday = datetime(2026, 9, 7, 21, tzinfo=UTC)  # Labor Day
    _run(db, start=holiday - timedelta(hours=1), finish=holiday - timedelta(hours=1))
    db.commit()
    status = FullSyncSchedule(db).due(at=holiday)
    assert status.market_ready_at == datetime(2026, 9, 4, 21, tzinfo=UTC)
    assert not status.due
    calendar = UsEquityMarketCalendar(extra_holidays=(date(2026, 9, 8),))
    following = holiday + timedelta(days=1)
    _run(db, start=following - timedelta(hours=1), finish=following - timedelta(hours=1))
    db.commit()
    assert not FullSyncSchedule(db, calendar=calendar).due(at=following).due
    assert FullSyncSchedule(db).due(at=following).market_due


def test_restart_after_downtime_reads_durable_success_and_crash_does_not_hide_due(
    tmp_path: Path,
) -> None:
    path = tmp_path / "schedule.db"
    db = sqlite3.connect(path)
    migrate(db)
    _run(db)
    db.commit()
    _run(db, start=FRIDAY_READY + timedelta(days=1), finish=None, ok=None)
    db.commit()  # The worker dies with this attempt still in flight.
    db.close()
    reopened = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        status = FullSyncSchedule(reopened).due(at=FRIDAY_READY + timedelta(days=2))
        assert status.elapsed_due and status.due
        assert status.last_successful_finish == FRIDAY_READY
    finally:
        reopened.close()


def test_uncommitted_success_does_not_delay_other_reader(tmp_path: Path) -> None:
    path = tmp_path / "committed.db"
    writer = sqlite3.connect(path)
    migrate(writer)
    reader = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        writer.execute("BEGIN IMMEDIATE")
        _run(writer)
        assert FullSyncSchedule(reader).due(at=FRIDAY_READY).due
        with pytest.raises(ScheduleStateError, match="no active transaction"):
            FullSyncSchedule(writer).due(at=FRIDAY_READY)
        writer.commit()
        assert not FullSyncSchedule(reader).due(at=FRIDAY_READY).due
    finally:
        writer.close()
        reader.close()


@pytest.mark.parametrize(
    ("start", "finish"),
    [
        ("invalid-private-row-text", "2026-09-18T21:00:00Z"),
        ("2026-09-18T21:00:00Z", "2026-09-18T21:00:00"),
        ("2026-09-18T21:00:00Z", "2026-09-18T21:00:00-01:00"),
        ("2026-09-18T21:00:00Z", "2026-09-18T20:59:59Z"),
        ("2026-09-18T21:00:00Z", "2026-09-19T21:00:00Z"),
    ],
)
def test_bad_success_clocks_refuse_without_echoing_stored_content(
    db: sqlite3.Connection, start: str, finish: str
) -> None:
    _run(db)
    db.execute("UPDATE sync_run SET started_at = ?, finished_at = ?", (start, finish))
    db.commit()
    with pytest.raises(ScheduleStateError) as caught:
        FullSyncSchedule(db).due(at=FRIDAY_READY)
    assert start not in str(caught.value)
    assert finish not in str(caught.value)


@pytest.mark.parametrize(
    "at",
    [FRIDAY_READY.replace(tzinfo=None), FRIDAY_READY.astimezone(ZoneInfo("Asia/Kolkata"))],
)
def test_check_time_must_be_aware_utc(db: sqlite3.Connection, at: datetime) -> None:
    with pytest.raises(ValueError):
        FullSyncSchedule(db).due(at=at)
