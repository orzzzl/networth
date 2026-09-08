"""SQLite writes that the restricted backup command is allowed to make."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from networth.storage import migrate

ARCHIVE_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")
ARCHIVE_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
ARCHIVE_ID_NOTICE_PREFIX = "backup-ssh-dispatch: archive_id="
VERIFIED = "VERIFIED"
FAILED = "FAILED"
VERDICTS = frozenset({VERIFIED, FAILED})
PULLER_NAME = "zelengs-macbook-air-2"


class BackupStateError(RuntimeError):
    """A backup state transition is malformed or names no existing archive."""


@dataclass(frozen=True, slots=True)
class ProbeState:
    generation: int
    built_at: datetime | None
    refusal_count: int


@dataclass(frozen=True, slots=True)
class BackupStatus:
    last_successful_backup: datetime | None
    key_escrow_confirmed_at: datetime | None
    last_verified_restore_at: datetime | None
    last_verified_restore_archive_id: str | None
    last_verified_restore_error: str | None
    probe_generation: int
    probe_refusal_count: int
    dispatch_rejection_count: int


def timestamp_to_db(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamp must be aware UTC")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def timestamp_from_db(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BackupStateError("stored backup timestamp is not UTC")
    try:
        return datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        raise BackupStateError("stored backup timestamp is not ISO-8601") from None


def validate_archive_id(archive_id: str) -> str:
    if ARCHIVE_ID_RE.fullmatch(archive_id) is None:
        raise BackupStateError("archive_id must be 32 lowercase hexadecimal characters")
    return archive_id


def validate_verdict(verdict: str) -> str:
    if verdict not in VERDICTS:
        raise BackupStateError("verdict must be VERIFIED or FAILED")
    return verdict


def open_database(path: Path) -> sqlite3.Connection:
    """Open and migrate one configured database, never an implicit fallback."""

    connection = sqlite3.connect(path, timeout=5.0)
    migrate(connection)
    return connection


class BackupStateStore:
    """The narrow row-level authority granted to backup commands."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def probe(self) -> ProbeState:
        row = self._connection.execute(
            "SELECT probe_generation, probe_built_at, probe_refusal_count "
            "FROM backup_state WHERE id = 1"
        ).fetchone()
        if row is None:
            raise BackupStateError("backup_state singleton is missing")
        return ProbeState(int(row[0]), timestamp_from_db(row[1]), int(row[2]))

    def mark_probe_built(self, *, generation: int, built_at: datetime) -> None:
        if generation <= 0:
            raise ValueError("probe generation must be positive")
        cursor = self._connection.execute(
            """
            UPDATE backup_state
            SET probe_generation = ?, probe_built_at = ?
            WHERE id = 1 AND probe_generation = ?
            """,
            (generation, timestamp_to_db(built_at), generation - 1),
        )
        if cursor.rowcount != 1:
            raise BackupStateError("probe generation changed while a build held the file lock")

    def count_probe_refusal(self) -> None:
        self._connection.execute(
            "UPDATE backup_state SET probe_refusal_count = probe_refusal_count + 1 WHERE id = 1"
        )

    def count_dispatch_rejection(self) -> None:
        self._connection.execute(
            "UPDATE backup_state "
            "SET dispatch_rejection_count = dispatch_rejection_count + 1 WHERE id = 1"
        )

    def insert_archive(
        self,
        *,
        archive_id: str,
        built_at: datetime,
        archive_sha256: str,
        byte_size: int,
        manifest_sha256: str,
    ) -> None:
        validate_archive_id(archive_id)
        self._connection.execute(
            """
            INSERT INTO backup_archive(
                archive_id, built_at, archive_sha256, byte_size, manifest_sha256
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                archive_id,
                timestamp_to_db(built_at),
                archive_sha256,
                byte_size,
                manifest_sha256,
            ),
        )

    def archive_id_for_transfer(self, *, archive_sha256: str, byte_size: int) -> str:
        """Identify the exact current file from VPS-side transfer bookkeeping."""

        if ARCHIVE_SHA256_RE.fullmatch(archive_sha256) is None or byte_size < 0:
            raise BackupStateError("archive transfer identity is malformed")
        rows = self._connection.execute(
            "SELECT archive_id FROM backup_archive "
            "WHERE archive_sha256 = ? AND byte_size = ? LIMIT 2",
            (archive_sha256, byte_size),
        ).fetchall()
        if len(rows) != 1 or not isinstance(rows[0][0], str):
            raise BackupStateError("served archive has no unique bookkeeping row")
        return validate_archive_id(rows[0][0])

    def record_pull(
        self,
        *,
        archive_id: str,
        verdict: str,
        pulled_by: str,
        at: datetime,
    ) -> None:
        validate_archive_id(archive_id)
        validate_verdict(verdict)
        if pulled_by != PULLER_NAME:
            raise BackupStateError(f"pulled_by must be the full name {PULLER_NAME!r}")

        if verdict == VERIFIED:
            cursor = self._connection.execute(
                """
                UPDATE backup_archive
                SET pulled_verified_at = coalesce(pulled_verified_at, ?),
                    pulled_by = coalesce(pulled_by, ?),
                    verify_error = NULL
                WHERE archive_id = ?
                """,
                (timestamp_to_db(at), pulled_by, archive_id),
            )
        else:
            cursor = self._connection.execute(
                """
                UPDATE backup_archive
                SET verify_error = CASE
                    WHEN pulled_verified_at IS NULL THEN 'destination verification failed'
                    ELSE verify_error
                END
                WHERE archive_id = ?
                """,
                (archive_id,),
            )
        if cursor.rowcount != 1:
            raise BackupStateError("record-pull names no existing archive")

    def record_drill(self, *, archive_id: str, verdict: str, at: datetime) -> None:
        validate_archive_id(archive_id)
        validate_verdict(verdict)
        exists = self._connection.execute(
            "SELECT 1 FROM backup_archive WHERE archive_id = ?", (archive_id,)
        ).fetchone()
        if exists is None:
            raise BackupStateError("record-drill names no existing archive")
        if verdict == VERIFIED:
            self._connection.execute(
                """
                UPDATE backup_state
                SET last_verified_restore_at = ?,
                    last_verified_restore_archive_id = ?,
                    last_verified_restore_error = NULL
                WHERE id = 1
                """,
                (timestamp_to_db(at), archive_id),
            )
        else:
            self._connection.execute(
                """
                UPDATE backup_state
                SET last_verified_restore_error = 'restore verification failed'
                WHERE id = 1
                """
            )

    def attest_key(self, *, at: datetime) -> None:
        self._connection.execute(
            "UPDATE backup_state SET key_escrow_confirmed_at = ? WHERE id = 1",
            (timestamp_to_db(at),),
        )

    def status(self) -> BackupStatus:
        state = self._connection.execute(
            """
            SELECT key_escrow_confirmed_at,
                   last_verified_restore_at,
                   last_verified_restore_archive_id,
                   last_verified_restore_error,
                   probe_generation,
                   probe_refusal_count,
                   dispatch_rejection_count
            FROM backup_state WHERE id = 1
            """
        ).fetchone()
        if state is None:
            raise BackupStateError("backup_state singleton is missing")
        last_pull = self._connection.execute(
            "SELECT max(pulled_verified_at) FROM backup_archive"
        ).fetchone()
        if last_pull is None:
            raise BackupStateError("backup archive status query returned no row")
        return BackupStatus(
            last_successful_backup=timestamp_from_db(last_pull[0]),
            key_escrow_confirmed_at=timestamp_from_db(state[0]),
            last_verified_restore_at=timestamp_from_db(state[1]),
            last_verified_restore_archive_id=cast("str | None", state[2]),
            last_verified_restore_error=cast("str | None", state[3]),
            probe_generation=int(state[4]),
            probe_refusal_count=int(state[5]),
            dispatch_rejection_count=int(state[6]),
        )
