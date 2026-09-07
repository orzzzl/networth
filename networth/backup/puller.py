"""Atomic Mac-side receipt, verification, journaling, and write-back retry."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from networth.backup.archive import (
    CURRENT_ARCHIVE,
    ArchiveKind,
    ProbeOutcome,
    verify_archive,
)
from networth.backup.state import VERIFIED
from networth.backup.transport import RemoteProbe, TransportError

PENDING_REPORTS = ".pending-backup-reports.json"
PULL_JOURNAL = "pull-runs.jsonl"


class BackupTransport(Protocol):
    def fetch_archive(self, kind: ArchiveKind, destination: Path) -> bool: ...

    def build_probe(self) -> RemoteProbe: ...

    def record_pull(self, archive_id: str, verdict: str) -> None: ...

    def record_drill(self, archive_id: str, verdict: str) -> None: ...


class PowerSource(StrEnum):
    BATTERY = "BATTERY"
    AC = "AC"
    UPS = "UPS"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class PendingReport:
    kind: str
    archive_id: str
    verdict: str


@dataclass(frozen=True, slots=True)
class PullResult:
    archive_id: str
    transferred: bool
    report_recorded: bool
    power_source: PowerSource


@dataclass(frozen=True, slots=True)
class CanaryResult:
    probe_generation: int
    local_observed_at: datetime


def read_power_source() -> PowerSource:
    """Read ``pmset`` on every run; UNKNOWN is measured failure, not inference."""

    try:
        result = subprocess.run(  # noqa: S603
            ["pmset", "-g", "batt"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return PowerSource.UNKNOWN
    if result.returncode != 0:
        return PowerSource.UNKNOWN
    first_line = result.stdout.splitlines()[0] if result.stdout.splitlines() else ""
    if "Battery Power" in first_line:
        return PowerSource.BATTERY
    if "AC Power" in first_line:
        return PowerSource.AC
    if "UPS Power" in first_line:
        return PowerSource.UPS
    return PowerSource.UNKNOWN


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class LocalBackupState:
    """Non-secret Mac evidence and reports that still need the VPS."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.pending_path = directory / PENDING_REPORTS
        self.journal_path = directory / PULL_JOURNAL

    def pending(self) -> list[PendingReport]:
        try:
            raw = json.loads(self.pending_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("local pending backup report state is unreadable") from exc
        if not isinstance(raw, list):
            raise RuntimeError("local pending backup report state has the wrong shape")
        reports: list[PendingReport] = []
        for entry in raw:
            if not isinstance(entry, dict) or set(entry) != {"archive_id", "kind", "verdict"}:
                raise RuntimeError("local pending backup report has the wrong shape")
            archive_id, kind, verdict = entry["archive_id"], entry["kind"], entry["verdict"]
            if not all(isinstance(value, str) and value for value in (archive_id, kind, verdict)):
                raise RuntimeError("local pending backup report has an invalid value")
            reports.append(PendingReport(kind, archive_id, verdict))
        return reports

    def replace_pending(self, reports: list[PendingReport]) -> None:
        temporary = self.directory / f".pending-reports-{uuid.uuid4().hex}"
        body = json.dumps(
            [
                {"archive_id": report.archive_id, "kind": report.kind, "verdict": report.verdict}
                for report in reports
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(body)
                handle.flush()
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, self.pending_path)
        _fsync_directory(self.directory)

    def add_pending(self, report: PendingReport) -> None:
        reports = self.pending()
        if report not in reports:
            reports.append(report)
            self.replace_pending(reports)

    def journal(self, record: dict[str, object]) -> None:
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        fd = os.open(
            self.journal_path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "ab", closefd=False) as handle:
                handle.write(line.encode())
                handle.flush()
            os.fsync(fd)
        finally:
            os.close(fd)


def send_report(transport: BackupTransport, report: PendingReport) -> None:
    if report.kind == "pull":
        transport.record_pull(report.archive_id, report.verdict)
    elif report.kind == "drill":
        transport.record_drill(report.archive_id, report.verdict)
    else:
        raise RuntimeError("unknown local backup report kind")


def flush_pending_reports(state: LocalBackupState, transport: BackupTransport) -> None:
    remaining: list[PendingReport] = []
    for report in state.pending():
        try:
            send_report(transport, report)
        except TransportError:
            remaining.append(report)
    state.replace_pending(remaining)


class BackupPuller:
    def __init__(
        self,
        *,
        transport: BackupTransport,
        destination: Path,
        backup_key: bytes,
        clock: Callable[[], datetime] | None = None,
        power_reader: Callable[[], PowerSource] = read_power_source,
    ) -> None:
        self.transport = transport
        self.state = LocalBackupState(destination)
        self._backup_key = backup_key
        self._clock = clock or (lambda: datetime.now(UTC))
        self._power_reader = power_reader

    def flush_pending(self) -> None:
        flush_pending_reports(self.state, self.transport)

    def run_once(self) -> PullResult:
        started_at = self._clock()
        power = self._power_reader()
        temporary = self.state.directory / f".tmp-pull-{uuid.uuid4().hex}"
        destination = self.state.directory / CURRENT_ARCHIVE
        transferred = False
        archive_id: str | None = None
        verified_copy = False
        try:
            self.flush_pending()
            transferred = self.transport.fetch_archive(ArchiveKind.CURRENT, temporary)
            candidate = temporary if transferred else destination
            if transferred:
                fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            verified = verify_archive(candidate, self._backup_key)
            if verified.manifest.archive_kind is not ArchiveKind.CURRENT:
                raise RuntimeError("pull received a probe where the current archive belongs")
            archive_id = verified.manifest.archive_id
            if transferred:
                os.replace(temporary, destination)
                _fsync_directory(self.state.directory)
            verified_copy = True
            report = PendingReport("pull", archive_id, VERIFIED)
            self.state.add_pending(report)
            recorded = True
            try:
                send_report(self.transport, report)
            except TransportError:
                recorded = False
            if recorded:
                self.state.replace_pending(
                    [pending for pending in self.state.pending() if pending != report]
                )
            self.state.journal(
                {
                    "archive_id": archive_id,
                    "power_source": power.value,
                    "recorded": recorded,
                    "run_at": started_at.isoformat(),
                    "transferred": transferred,
                    "verified": True,
                }
            )
            return PullResult(archive_id, transferred, recorded, power)
        except BaseException:
            temporary.unlink(missing_ok=True)
            self.state.journal(
                {
                    "archive_id": archive_id,
                    "power_source": power.value,
                    "recorded": False,
                    "run_at": started_at.isoformat(),
                    "transferred": transferred,
                    "verified": verified_copy,
                }
            )
            raise


def run_canary(
    *,
    transport: BackupTransport,
    destination: Path,
    backup_key: bytes,
    local_observed_at: datetime,
    wait: Callable[[float], None] = time.sleep,
    maximum_attempts: int = 3,
) -> CanaryResult:
    """Require ``built`` then verify that exact VPS-local generation.

    ``local_observed_at`` is returned as audit metadata only.  It is never
    compared with a VPS timestamp, which is the issue #9 invariant.
    """

    if maximum_attempts <= 0:
        raise ValueError("maximum_attempts must be positive")
    state = LocalBackupState(destination)
    built: RemoteProbe | None = None
    for attempt in range(maximum_attempts):
        response = transport.build_probe()
        if response.outcome is ProbeOutcome.BUILT:
            built = response
            break
        if attempt + 1 < maximum_attempts:
            wait(60.0)
    if built is None:
        raise RuntimeError("backup canary never obtained its own probe build")

    temporary = state.directory / f".tmp-canary-{uuid.uuid4().hex}"
    try:
        if not transport.fetch_archive(ArchiveKind.PROBE, temporary):
            raise RuntimeError("backup canary transport did not return its probe")
        verified = verify_archive(temporary, backup_key)
        if (
            verified.manifest.archive_kind is not ArchiveKind.PROBE
            or verified.manifest.probe_generation != built.probe_generation
        ):
            raise RuntimeError("backup canary verified a different probe generation")
        return CanaryResult(built.probe_generation, local_observed_at)
    finally:
        temporary.unlink(missing_ok=True)
        _fsync_directory(state.directory)
