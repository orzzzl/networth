"""Task 20: publication freshness comes only from committed successes."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from networth.publication import PublicationMonitor, PublicationMonitorError
from networth.publisher import GRACE_SECONDS, PUBLISH_INTERVAL_SECONDS
from networth.storage import migrate

PUBLISHED_AT = datetime(2026, 9, 16, 8, 0, tzinfo=UTC)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _setup_parent_rows(connection: sqlite3.Connection) -> None:
    stamp = _timestamp(PUBLISHED_AT)
    connection.execute(
        'INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok) '
        "VALUES ('run-publication-monitor', ?, ?, 'TEST', 1)",
        (stamp, stamp),
    )
    connection.execute(
        """
        INSERT INTO snapshot(
            sync_run_id, taken_at, total_net_worth_minor, total_assets_minor,
            total_liabilities_minor, account_count, stale_account_count,
            unknown_freshness_account_count, static_account_count,
            reauth_account_count, unreconciled_account_count, is_complete,
            age_state, as_of, oldest_known_source_as_of
        ) VALUES ('run-publication-monitor', ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1,
                  'STATIC_ONLY', NULL, NULL)
        """,
        (stamp,),
    )
    connection.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) "
        "VALUES ('pair-publication-monitor', ?, 'payload-key/monitor', 'ACTIVE')",
        (stamp,),
    )


def _insert_success(connection: sqlite3.Connection, *, seq: int, at: datetime) -> None:
    connection.execute(
        """
        INSERT INTO publication(snapshot_id, pairing_id, seq, schema_version, published_at)
        VALUES (1, 'pair-publication-monitor', ?, '1', ?)
        """,
        (seq, _timestamp(at)),
    )


def test_no_success_is_overdue_without_inventing_an_age() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        migrate(connection)
        status = PublicationMonitor(connection).status(at=PUBLISHED_AT)
    finally:
        connection.close()

    assert status.last_successful_at is None
    assert status.age is None
    assert status.overdue is True


def test_deadline_is_interval_plus_grace_and_only_older_is_overdue() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        migrate(connection)
        _setup_parent_rows(connection)
        _insert_success(connection, seq=1, at=PUBLISHED_AT)
        connection.commit()
        monitor = PublicationMonitor(connection)
        deadline = PUBLISHED_AT + timedelta(seconds=PUBLISH_INTERVAL_SECONDS + GRACE_SECONDS)

        at_deadline = monitor.status(at=deadline)
        after_deadline = monitor.status(at=deadline + timedelta(microseconds=1))
    finally:
        connection.close()

    assert at_deadline.last_successful_at == PUBLISHED_AT
    assert at_deadline.age == timedelta(seconds=PUBLISH_INTERVAL_SECONDS + GRACE_SECONDS)
    assert at_deadline.overdue is False
    assert after_deadline.overdue is True


def test_monitor_reads_last_committed_success_not_an_uncommitted_attempt(
    tmp_path: Path,
) -> None:
    database = tmp_path / "networth.db"
    writer = sqlite3.connect(database)
    reader: sqlite3.Connection | None = None
    try:
        migrate(writer)
        _setup_parent_rows(writer)
        writer.commit()
        reader = sqlite3.connect(database)
        monitor = PublicationMonitor(reader)

        writer.execute("BEGIN IMMEDIATE")
        _insert_success(writer, seq=1, at=PUBLISHED_AT)
        before_commit = monitor.status(at=PUBLISHED_AT + timedelta(hours=1))
        writer.commit()
        after_commit = monitor.status(at=PUBLISHED_AT + timedelta(hours=1))
    finally:
        if reader is not None:
            reader.close()
        writer.close()

    assert before_commit.last_successful_at is None
    assert before_commit.overdue is True
    assert after_commit.last_successful_at == PUBLISHED_AT
    assert after_commit.age == timedelta(hours=1)
    assert after_commit.overdue is False


def test_monitor_refuses_to_call_uncommitted_rows_committed() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        migrate(connection)
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(PublicationMonitorError, match="no active transaction"):
            PublicationMonitor(connection).status(at=PUBLISHED_AT)
        connection.rollback()
    finally:
        connection.close()
