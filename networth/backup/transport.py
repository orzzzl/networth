"""Mac-initiated SSH transport for the four forced-command verbs."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from networth.backup.archive import ArchiveKind, ProbeOutcome
from networth.backup.state import (
    ARCHIVE_ID_NOTICE_PREFIX,
    PULLER_NAME,
    BackupStateError,
    validate_archive_id,
    validate_verdict,
)

_HOST_RE = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252}[A-Za-z0-9])?\Z")
_USER_RE = re.compile(r"\A[a-z_][a-z0-9_-]{0,31}\Z")


class TransportError(RuntimeError):
    """The restricted SSH path did not complete one bounded operation."""


@dataclass(frozen=True, slots=True)
class RemoteProbe:
    probe_generation: int
    outcome: ProbeOutcome


@dataclass(frozen=True, slots=True)
class FetchedArchive:
    """VPS-side identity carried outside the sealed bytes over authenticated SSH."""

    archive_id: str | None


class SshTransport:
    """No remote shell: every command is one dispatcher grammar string."""

    def __init__(self, *, host: str, user: str, identity: Path, timeout: int = 30) -> None:
        if _HOST_RE.fullmatch(host) is None or _USER_RE.fullmatch(user) is None:
            raise ValueError("SSH host or user is outside the restricted name grammar")
        if timeout <= 0:
            raise ValueError("SSH timeout must be positive")
        self._target = f"{user}@{host}"
        self._identity = identity
        self._timeout = timeout

    def _argv(self, original_command: str) -> list[str]:
        return [
            "ssh",
            "-i",
            str(self._identity),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            f"ConnectTimeout={self._timeout}",
            "--",
            self._target,
            original_command,
        ]

    def fetch_archive(self, kind: ArchiveKind, destination: Path) -> FetchedArchive:
        command = f"serve-archive {kind.value}"
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            result = subprocess.run(  # noqa: S603
                self._argv(command),
                stdout=fd,
                stderr=subprocess.PIPE,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            destination.unlink(missing_ok=True)
            raise TransportError(f"SSH {kind.value} archive fetch did not complete") from exc
        finally:
            os.close(fd)
        if result.returncode != 0:
            destination.unlink(missing_ok=True)
            raise TransportError(
                f"SSH {kind.value} archive fetch failed with exit {result.returncode}"
            )
        archive_id: str | None = None
        if kind is ArchiveKind.CURRENT:
            prefix = ARCHIVE_ID_NOTICE_PREFIX.encode("ascii")
            matches = [
                line[len(prefix) :]
                for line in result.stderr.splitlines()
                if line.startswith(prefix)
            ]
            if len(matches) != 1:
                destination.unlink(missing_ok=True)
                raise TransportError("SSH current archive fetch omitted its transfer identity")
            try:
                archive_id = validate_archive_id(matches[0].decode("ascii"))
            except (BackupStateError, UnicodeDecodeError):
                destination.unlink(missing_ok=True)
                raise TransportError(
                    "SSH current archive fetch returned an invalid identity"
                ) from None
        return FetchedArchive(archive_id)

    def build_probe(self) -> RemoteProbe:
        try:
            result = subprocess.run(  # noqa: S603
                self._argv("build-probe"),
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TransportError("SSH build-probe did not complete") from exc
        if result.returncode != 0:
            raise TransportError(f"SSH build-probe failed with exit {result.returncode}")
        try:
            raw = json.loads(result.stdout)
            generation = raw["probe_generation"]
            outcome = ProbeOutcome(raw["outcome"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise TransportError("SSH build-probe returned an invalid response") from None
        if (
            not isinstance(raw, dict)
            or set(raw) != {"outcome", "probe_generation"}
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation <= 0
        ):
            raise TransportError("SSH build-probe returned an invalid response")
        return RemoteProbe(generation, outcome)

    def record_pull(self, archive_id: str, verdict: str) -> None:
        validate_archive_id(archive_id)
        validate_verdict(verdict)
        self._record(f"record-pull {archive_id} {verdict} {PULLER_NAME}", "record-pull")

    def record_drill(self, archive_id: str, verdict: str) -> None:
        validate_archive_id(archive_id)
        validate_verdict(verdict)
        self._record(f"record-drill {archive_id} {verdict}", "record-drill")

    def _record(self, original_command: str, verb: str) -> None:
        try:
            result = subprocess.run(  # noqa: S603
                self._argv(original_command),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TransportError(f"SSH {verb} did not complete") from exc
        if result.returncode != 0:
            raise TransportError(f"SSH {verb} failed with exit {result.returncode}")
