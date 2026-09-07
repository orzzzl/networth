"""The forced-command allow-list; remote input is never evaluated by a shell."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, TextIO

from networth.backup.archive import (
    CURRENT_ARCHIVE,
    PROBE_ARCHIVE,
    ArchiveError,
    BackupBuilder,
    ProbeBusyError,
    ProbeOutcome,
)
from networth.backup.state import (
    PULLER_NAME,
    BackupStateError,
    BackupStateStore,
    open_database,
    validate_archive_id,
    validate_verdict,
)

_SAFE_VERB = re.compile(r"\A[a-z-]+\Z")
DISPATCHER_INSTALL_PATH = Path("/usr/local/lib/networth/backup-ssh-dispatch")


class BackupDispatcher:
    """Allow exactly four verbs and their closed argument grammars."""

    def __init__(
        self,
        *,
        builder: BackupBuilder,
        database: Path,
        stdout: BinaryIO | None = None,
        stderr: TextIO | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.builder = builder
        self.database = database
        self.stdout = sys.stdout.buffer if stdout is None else stdout
        self.stderr = sys.stderr if stderr is None else stderr
        self.clock = clock or (lambda: datetime.now(UTC))

    def _reject(self, verb: str) -> int:
        rendered = verb if _SAFE_VERB.fullmatch(verb) else "<invalid>"
        try:
            with closing(open_database(self.database)) as connection:
                BackupStateStore(connection).count_dispatch_rejection()
                connection.commit()
        except Exception:
            # The rejection still fails closed if bookkeeping is unavailable;
            # stderr is captured by the service journal and names no argument.
            pass
        print(f"backup-ssh-dispatch: rejected verb={rendered}", file=self.stderr)
        return 64

    def _serve(self, name: str) -> int:
        filename = CURRENT_ARCHIVE if name == "current" else PROBE_ARCHIVE
        path = self.builder.archive_dir / filename
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            print("backup-ssh-dispatch: requested archive is unavailable", file=self.stderr)
            return 1
        try:
            with os.fdopen(fd, "rb", closefd=False) as source:
                while chunk := source.read(1024 * 1024):
                    self.stdout.write(chunk)
            self.stdout.flush()
        finally:
            os.close(fd)
        return 0

    def dispatch(self, original_command: str | None) -> int:
        if not original_command or "\n" in original_command or "\r" in original_command:
            return self._reject("<empty>")
        parts = original_command.split(" ")
        if any(not part for part in parts):
            return self._reject(parts[0] if parts else "<empty>")
        verb = parts[0]
        try:
            if parts == ["build-probe"]:
                result = self.builder.build_probe(now=self.clock())
                if result.outcome is ProbeOutcome.REUSED:
                    print(
                        "backup-ssh-dispatch: build-probe cooldown refusal outcome=reused",
                        file=self.stderr,
                    )
                response = json.dumps(
                    {
                        "outcome": result.outcome.value,
                        "probe_generation": result.probe_generation,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                self.stdout.write(response + b"\n")
                self.stdout.flush()
                return 0
            if parts in (["serve-archive", "current"], ["serve-archive", "probe"]):
                return self._serve(parts[1])
            if len(parts) == 4 and verb == "record-pull":
                archive_id = validate_archive_id(parts[1])
                verdict = validate_verdict(parts[2])
                if parts[3] != PULLER_NAME:
                    return self._reject(verb)
                with closing(open_database(self.database)) as connection:
                    BackupStateStore(connection).record_pull(
                        archive_id=archive_id,
                        verdict=verdict,
                        pulled_by=parts[3],
                        at=self.clock(),
                    )
                    connection.commit()
                return 0
            if len(parts) == 3 and verb == "record-drill":
                archive_id = validate_archive_id(parts[1])
                verdict = validate_verdict(parts[2])
                with closing(open_database(self.database)) as connection:
                    BackupStateStore(connection).record_drill(
                        archive_id=archive_id,
                        verdict=verdict,
                        at=self.clock(),
                    )
                    connection.commit()
                return 0
        except ProbeBusyError:
            print("backup-ssh-dispatch: build-probe single-flight refusal", file=self.stderr)
            return 75
        except (ArchiveError, BackupStateError, OSError, ValueError):
            return self._reject(verb)
        return self._reject(verb)
