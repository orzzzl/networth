"""Weekly offline drill whose report is retryable bookkeeping, not its verdict."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from networth.backup.puller import (
    BackupTransport,
    LocalBackupState,
    PendingReport,
    flush_pending_reports,
)
from networth.backup.restore import restore_archive, restored_files_are_private
from networth.backup.state import VERIFIED


@dataclass(frozen=True, slots=True)
class DrillResult:
    archive_id: str
    verified: bool
    report_recorded: bool
    orphan_token_count: int


def run_restore_drill(
    *,
    archive: Path,
    backup_key: bytes,
    local_state_directory: Path,
    transport: BackupTransport | None,
    now: datetime | None = None,
) -> DrillResult:
    """Verify and restore entirely offline, then separately attempt reporting."""

    chosen_now = datetime.now(UTC) if now is None else now
    with tempfile.TemporaryDirectory(prefix="networth-restore-drill-") as temporary:
        restored = restore_archive(
            archive,
            backup_key,
            Path(temporary) / "restored",
            now=chosen_now,
            reason="weekly restore drill",
        )
        if not restored_files_are_private(restored) or not restored.replay.passed:
            raise RuntimeError("restored archive did not pass the offline drill")

        state = LocalBackupState(local_state_directory)
        report = PendingReport("drill", restored.archive_id, VERIFIED)
        state.add_pending(report)
        if transport is not None:
            flush_pending_reports(state, transport)
        recorded = report not in state.pending()
        return DrillResult(
            restored.archive_id,
            True,
            recorded,
            restored.orphan_token_count,
        )
