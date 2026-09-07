"""Offline restore and drill logic for the copy held on the Mac."""

from __future__ import annotations

import os
import sqlite3
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from networth.backup.archive import (
    ArchiveKind,
    ArchiveVerificationError,
    opened_archive_for_restore,
)
from networth.backup.replay import ReplayDrillResult, run_replay_drill
from networth.backup.state import timestamp_to_db


class RestoreError(RuntimeError):
    """A destination cannot accept a verified archive without overwriting state."""


@dataclass(frozen=True, slots=True)
class RestoreResult:
    archive_id: str
    destination: Path
    database: Path
    token_store: Path
    publish_epoch: int
    orphan_token_count: int
    replay: ReplayDrillResult


def _secure_write(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _prepare_database(database: Path, *, now: datetime, reason: str) -> tuple[int, int]:
    if not reason:
        raise ValueError("restore reason cannot be empty")
    connection = sqlite3.connect(database)
    try:
        prior_row = connection.execute(
            "SELECT publish_epoch FROM daemon_state WHERE id = 1"
        ).fetchone()
        if prior_row is None:
            raise RestoreError("restored database has no daemon_state singleton")
        prior_epoch = int(prior_row[0])
        seq_row = connection.execute("SELECT coalesce(max(seq), 0) FROM publication").fetchone()
        if seq_row is None:
            raise RestoreError("restored database did not return its publication counter")
        restored_seq = int(seq_row[0])
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM published_envelope")
        cursor = connection.execute(
            """
            UPDATE daemon_state
            SET publish_epoch = publish_epoch + 1,
                epoch_bumped_at = ?,
                epoch_bumped_reason = ?
            WHERE id = 1 AND publish_epoch = ?
            """,
            (timestamp_to_db(now), reason, prior_epoch),
        )
        if cursor.rowcount != 1:
            raise RestoreError("restore lineage changed during preparation")
        connection.commit()
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or int(checkpoint[0]) != 0:
            raise RestoreError("restored database could not checkpoint its preparation")
        return prior_epoch + 1, restored_seq
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def restore_archive(
    archive: Path,
    backup_key: bytes,
    destination: Path,
    *,
    now: datetime | None = None,
    reason: str = "restore drill",
) -> RestoreResult:
    """Restore into an absent or empty directory; never replace live state."""

    opened, verification = opened_archive_for_restore(archive, backup_key)
    if opened.manifest.archive_kind is not ArchiveKind.CURRENT:
        raise ArchiveVerificationError("a probe archive cannot be used as a restore source")
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise RestoreError("restore destination must be absent or an empty directory")
    else:
        destination.mkdir(mode=0o700, parents=True)
    os.chmod(destination, 0o700)

    database = destination / "networth.db"
    tokens = destination / "tokenstore"
    tokens.mkdir(mode=0o700)
    os.chmod(tokens, 0o700)
    try:
        _secure_write(database, opened.database)
        for name, data in sorted(opened.token_files.items()):
            target = tokens / name
            if target.parent != tokens or target.name != name:
                raise RestoreError("archived TokenStore name escaped its destination")
            _secure_write(target, data)
        _fsync_directory(tokens)
        chosen_now = datetime.now(UTC) if now is None else now
        publish_epoch, restored_seq = _prepare_database(database, now=chosen_now, reason=reason)
        _fsync_directory(destination)
    except BaseException:
        # This destination was required to be empty.  Leave the partial restore
        # visible for diagnosis; silently deleting it would erase the evidence
        # that the operation did not finish.
        raise

    replay = run_replay_drill(restored_seq)
    if not replay.passed:
        raise RestoreError("pairing-scoped replay drill failed")
    return RestoreResult(
        archive_id=opened.manifest.archive_id,
        destination=destination,
        database=database,
        token_store=tokens,
        publish_epoch=publish_epoch,
        orphan_token_count=verification.orphan_token_count,
        replay=replay,
    )


def restored_files_are_private(result: RestoreResult) -> bool:
    """A narrow diagnostic used by the drill and tests, never a chmod repair."""

    paths = (result.destination, result.token_store, result.database, *result.token_store.iterdir())
    for path in paths:
        mode = stat.S_IMODE(path.stat().st_mode)
        expected = 0o700 if path.is_dir() else 0o600
        if mode != expected:
            return False
    return True
