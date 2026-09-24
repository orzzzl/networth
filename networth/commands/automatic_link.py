"""Verified holder driver: one mint, durable record, authenticated return, then URL.

The remote runner's *public source* is a shell argument; token/URL material is
only stdin/stdout pipes. Every remote output is captured, never forwarded. Both
halves run the named commit; a resumed process reads the original record only.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from networth import link_recovery, mac_identity
from networth.commands.authorize_automatic_link import ACK
from networth.link_recovery import MINT_WIRE_MARKER, MintResult, RecoveryRecord
from networth.plaid.environment import PlaidEnvironment, selected_environment

SUMMARY = "Start or resume automatic Sandbox Link on zelengs-macbook-air-2."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--commit", required=True, help="reviewed full commit, identical on both halves"
    )
    parser.add_argument("--resume", help="existing recovery-record flow UUID; never mints")


def _source(commit: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("full reviewed commit required")
    head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], check=True, capture_output=True, text=True
    )
    if head.stdout.strip() != commit or dirty.stdout.strip():
        raise ValueError("checkout must be clean at reviewed commit")
    return subprocess.run(
        ["git", "show", commit + ":scripts/sandbox-rehearsal.sh"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _remote(source: str, commit: str, verb: str, material: str = "") -> list[str]:
    if verb not in {"mint-automatic-link", "authorize-automatic-link"}:
        raise ValueError("unsupported automatic operation")
    # bash -c consumes code from argv, leaving SSH stdin for the attestation.
    # The existing measurement transport continues using bash -s unchanged.
    command = shlex.join(
        [
            "sudo",
            "-u",
            "networth",
            "-H",
            "bash",
            "-c",
            source,
            "automatic-link",
            commit,
            "--verb",
            verb,
        ]
    )
    result = subprocess.run(
        [
            "ssh",
            "-i",
            str(Path.home() / "agents/secrets/networth-vps.key"),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "root@100.102.245.37",
            command,
        ],
        input=material,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("automatic remote operation failed")
    return result.stdout.splitlines()


def _release(source: str, commit: str, record: RecoveryRecord) -> None:
    lines = _remote(source, commit, "authorize-automatic-link", record.to_json())
    acknowledgements = [line for line in lines if line.startswith(ACK)]
    if acknowledgements != [ACK + record.flow_id]:
        raise ValueError("fresh release acknowledgement is missing")


def run(args: argparse.Namespace) -> int:
    try:
        if selected_environment() is not PlaidEnvironment.SANDBOX:
            raise ValueError("automatic driver permits Sandbox only")
        holder = mac_identity.verify()
        source = _source(args.commit)
        directory = link_recovery.mac_recovery_directory()
        if args.resume:
            record = link_recovery.verify_for_resume(
                directory, args.resume, holder=holder, now=datetime.now(UTC)
            )
        else:
            lines = _remote(source, args.commit, "mint-automatic-link")
            payloads = [line for line in lines if line.startswith(MINT_WIRE_MARKER)]
            if len(payloads) != 1:
                raise ValueError("mint payload missing or ambiguous")
            mint = MintResult.from_wire(payloads[0])
            record = link_recovery.store_and_verify(
                directory,
                mint.as_record(now=datetime.now(UTC), automatic=True),
                holder=holder,
                now=datetime.now(UTC),
            )
        _release(source, args.commit, record)
        # Fresh ack on every path, even a record whose previous ack was lost.
        # Never retire this record on one child's exchange: the URL has sessions.
        assert record.hosted_url is not None
        print(record.hosted_url.reveal())
        return 0
    except Exception:
        print(
            "Automatic Link stopped without displaying a URL. Retain any existing recovery "
            "record; resume that flow after inspection.",
            file=sys.stderr,
        )
        return 2
