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
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "link-recover.sh"
FLOW_ID = "0123456789abcdef0123456789abcdef"


def uv_stub(tmp_path: Path) -> tuple[Path, Path]:
    """A `uv` that records the argv it was asked to run instead of running it.

    **`verify-this-mac` is the one exception and runs for real.** It is the
    pre-flight this script gained in the PR #75 re-review, and a stub that
    returned 0 for it would make the refusal it exists for impossible to test —
    a check whose failing branch is unreachable from the suite is the shape of
    evidence this project keeps rejecting elsewhere. Everything after it is still
    a recording stub, so no Plaid call is reachable from here either way.
    """
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir(exist_ok=True)
    argv_log = tmp_path / "uv.argv"
    stub = stub_dir / "uv"
    stub.write_text(
        "#!/bin/sh\n"
        'case " $* " in\n'
        f'*" verify-this-mac "*) exec {sys.executable} -m networth verify-this-mac ;;\n'
        "esac\n"
        f"printf '%s\\n' \"$@\" > {argv_log}\nexit 0\n"
    )
    stub.chmod(0o755)
    return stub_dir, argv_log


#: Every machine holds `127.0.0.1` and none holds `192.0.2.1` (RFC 5737), so the
#: pre-flight's bind stays a real measurement on any runner while the identity it
#: requires becomes something a test can choose.
ON_THIS_MACHINE = {
    "NETWORTH_MAC_IDENTITY_ADDRESS": "127.0.0.1",
    "NETWORTH_MAC_IDENTITY_HOLDER": "test-host",
}


def run(
    *args: str, tmp_path: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    stub_dir, _ = uv_stub(tmp_path)
    environment = {
        **os.environ,
        "PATH": f"{stub_dir}:{os.environ['PATH']}",
        "PYTHONPATH": str(REPO_ROOT),
        **ON_THIS_MACHINE,
    }
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
        env={
            **os.environ,
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "PYTHONPATH": str(REPO_ROOT),
            # This test builds its own environment rather than using `run()`, so it
            # has to pin the identity itself — without it the pre-flight passed only
            # on the developer's Mac and this was the single test that failed when
            # the suite was re-run against an address no machine holds.
            **ON_THIS_MACHINE,
        },
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


def test_the_wrong_machine_is_refused_before_anything_is_read_or_prompted_for(
    tmp_path: Path,
) -> None:
    """Measurement (iv) is "does this work **from the second host**".

    Run anywhere else it does not give a weaker answer to that question, it gives a
    wrong one and records it as evidence. Refusing here also means the two
    credential prompts never happen on a machine with no business collecting them.

    The proof that it refused *early* is that the recording stub was never reached:
    it writes its argv on every call, so the absence of that file is the absence of
    the recovery verb.
    """
    result = run(
        FLOW_ID,
        tmp_path=tmp_path,
        env={"NETWORTH_MAC_IDENTITY_ADDRESS": "192.0.2.1"},
    )

    assert result.returncode == 2
    assert "192.0.2.1" in result.stderr
    assert not (tmp_path / "uv.argv").exists(), "the recovery verb was reached anyway"
    assert "complete-hosted-link" not in result.stdout


def test_the_right_machine_still_reaches_the_recovery_verb(tmp_path: Path) -> None:
    """The other half, so the refusal above is a discriminator and not a wall."""
    result = run(FLOW_ID, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    argv = (tmp_path / "uv.argv").read_text().split()
    assert "complete-hosted-link" in argv
