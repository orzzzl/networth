"""What `scripts/link-recover.sh` refuses, and what it hands to the verb.

This script is task 06a's measurement (iv) **and** the first form of the command
`DESIGN.md` §19 step 2a names for the real emergency. Both readings put the same
requirement on it: the call path it runs must be the one `07b` extends, because a
rehearsal of some other path leaves the emergency inheriting something nothing has
ever run.

Nothing here reaches Plaid; the verb is replaced by a recording stub.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "link-recover.sh"
FLOW_ID = "0123456789abcdef0123456789abcdef"


def uv_stub(tmp_path: Path) -> tuple[Path, Path]:
    """A `uv` that records the argv it was asked to run instead of running it."""
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir(exist_ok=True)
    argv_log = tmp_path / "uv.argv"
    stub = stub_dir / "uv"
    stub.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {argv_log}\nexit 0\n")
    stub.chmod(0o755)
    return stub_dir, argv_log


def run(
    *args: str, tmp_path: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    stub_dir, _ = uv_stub(tmp_path)
    environment = {**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}"}
    environment.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=environment,
        timeout=120,
    )


def test_the_script_parses() -> None:
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_it_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK), "§19 tells the owner to run it directly"


@pytest.mark.parametrize("bad", ["", "0" * 31, "0" * 33, "A" * 32, "not-a-flow", "$(id)"])
def test_only_a_32_hex_flow_id_is_accepted(bad: str, tmp_path: Path) -> None:
    result = run(bad, tmp_path=tmp_path)

    assert result.returncode == 2
    assert "is not a flow id" in result.stderr or "usage:" in result.stderr


def test_a_second_argument_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    result = run(FLOW_ID, "--exchange-twice", tmp_path=tmp_path)

    assert result.returncode == 2
    assert "takes the flow id and nothing else" in result.stderr


def test_a_non_sandbox_environment_is_refused_rather_than_overridden(tmp_path: Path) -> None:
    """Pinned, not inherited. A rehearsal one exported variable away from Production
    is not a rehearsal, and this is the script the owner runs by hand."""
    result = run(FLOW_ID, tmp_path=tmp_path, env={"NETWORTH_ENV": "production"})

    assert result.returncode == 2
    assert "runs against sandbox and nothing else" in result.stderr


def test_it_invokes_the_two_prompt_recovery_path_and_fixes_the_mode(tmp_path: Path) -> None:
    """The exact call path, read off the argv rather than off the source.

    `--from-tty --flow <id>` is what makes the link token come from this Mac's
    recovery record and the two prompts come from the terminal — §19 step 2a's shape.
    `--exchange` is fixed rather than offered: in the emergency this is the first
    form of, there is one thing to do, and an operator reading it against a 30-minute
    clock should not be choosing a mode.
    """
    stub_dir, argv_log = uv_stub(tmp_path)
    result = subprocess.run(
        ["bash", str(SCRIPT), FLOW_ID],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}"},
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert argv_log.read_text().splitlines() == [
        "run",
        "--quiet",
        "networth",
        "complete-hosted-link",
        "--from-tty",
        "--flow",
        FLOW_ID,
        "--exchange",
    ]


def test_the_flow_id_is_the_only_thing_that_varies(tmp_path: Path) -> None:
    """A guard against the mode becoming a parameter by accident later: the argv
    above is the whole surface, and `--exchange-twice` would spend a second call on
    a path whose reason for existing is that something already went wrong."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "--exchange-twice" not in source
    assert "--retrieve-only" not in source
