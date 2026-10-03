"""Read diagnostics on one host; the local mode never opens a VPS database."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from datetime import UTC, datetime

from networth.commands._read import emit, execute
from networth.diagnostics import read_doctor
from networth.link_recovery import LinkRecoveryError, RecoveryRecord, mac_recovery_directory

SUMMARY = "Read sync-host diagnostics, or local recovery record deadlines with --local."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--local",
        action="store_true",
        help="only recovery records on this host; never contact the sync host",
    )


def _local() -> int:
    now = datetime.now(UTC)
    directory = mac_recovery_directory()
    records: list[dict[str, object]] = []
    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError:
        emit(
            {
                "scope": "LOCAL_RECOVERY_FILES",
                "checked_at": now,
                "directory_status": "ABSENT",
                "records": [],
            }
        )
        return 0
    except OSError:
        print("Local recovery directory cannot be inspected; presence is unknown.", file=sys.stderr)
        return 2
    failed = False
    for path in entries:
        if re.fullmatch(r"[0-9a-f]{32}\.json", path.name) is None:
            continue
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, encoding="utf-8") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("not regular")
                record = RecoveryRecord.from_json(stream.read())
            if record.flow_id != path.stem:
                raise ValueError("identity mismatch")
            records.append(
                {
                    "flow_id": record.flow_id,
                    "reap_after": record.reap_after,
                    "overdue": now >= record.reap_after,
                }
            )
        except (OSError, ValueError, LinkRecoveryError):
            failed = True
            records.append(
                {
                    "flow_id": path.stem,
                    "reap_after": None,
                    "status": "UNREADABLE: deadline unknown; record retained",
                }
            )
    emit(
        {
            "scope": "LOCAL_RECOVERY_FILES",
            "checked_at": now,
            "record_count": len(records),
            "records": records,
            "deadline_scope": "Local hygiene bound, not evidence of an exchange deadline.",
        }
    )
    return 1 if failed else 0


def run(args: argparse.Namespace) -> int:
    return _local() if args.local else execute(read_doctor)
