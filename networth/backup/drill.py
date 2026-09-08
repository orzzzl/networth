"""Weekly offline drill whose report is retryable bookkeeping, not its verdict."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from networth.backup.archive import CURRENT_ARCHIVE, ArchiveVerificationError, verify_archive
from networth.backup.puller import (
    BackupTransport,
    CurrentReceipt,
    LocalBackupState,
    PendingReport,
    flush_pending_reports,
)
from networth.backup.restore import restore_archive, restored_files_are_private
from networth.backup.state import FAILED, VERIFIED


@dataclass(frozen=True, slots=True)
class DrillResult:
    archive_id: str | None
    verified: bool
    report_recorded: bool
    orphan_token_count: int | None
    restore_preparation_passed: bool
    failure: str | None


def _record_verdict(
    *,
    state: LocalBackupState,
    transport: BackupTransport | None,
    report: PendingReport,
) -> bool:
    state.add_pending(report)
    if transport is not None:
        flush_pending_reports(state, transport)
    return report not in state.pending()


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
    state = LocalBackupState(local_state_directory)
    receipt: CurrentReceipt | None = None
    if archive.resolve(strict=False) == (state.directory / CURRENT_ARCHIVE).resolve(strict=False):
        receipt = state.current_receipt()
    archive_id = None if receipt is None else receipt.archive_id
    try:
        verification = verify_archive(archive, backup_key)
        if receipt is not None:
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            if (
                verification.manifest.archive_id != receipt.archive_id
                or digest != receipt.archive_sha256
            ):
                raise ArchiveVerificationError(
                    "drill archive does not match its durable pull receipt"
                )
        else:
            archive_id = verification.manifest.archive_id
        with tempfile.TemporaryDirectory(prefix="networth-restore-drill-") as temporary:
            restored = restore_archive(
                archive,
                backup_key,
                Path(temporary) / "restored",
                now=chosen_now,
                reason="weekly restore drill",
            )
            if not restored_files_are_private(restored) or not restored.preparation.passed:
                raise RuntimeError("restored archive did not pass the offline drill")
    except Exception as exc:
        recorded = False
        if archive_id is not None:
            recorded = _record_verdict(
                state=state,
                transport=transport,
                report=PendingReport("drill", archive_id, FAILED),
            )
        return DrillResult(
            archive_id=archive_id,
            verified=False,
            report_recorded=recorded,
            orphan_token_count=None,
            restore_preparation_passed=False,
            failure=type(exc).__name__,
        )

    report = PendingReport("drill", restored.archive_id, VERIFIED)
    recorded = _record_verdict(state=state, transport=transport, report=report)
    return DrillResult(
        archive_id=restored.archive_id,
        verified=True,
        report_recorded=recorded,
        orphan_token_count=restored.orphan_token_count,
        restore_preparation_passed=restored.preparation.passed,
        failure=None,
    )
