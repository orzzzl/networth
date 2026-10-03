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
from tests.mac_shim import mac_shim

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


def run(
    *args: str,
    tmp_path: Path,
    env: dict[str, str] | None = None,
    on_this_machine: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the driver with the Mac pre-flight substituted in the child.

    `on_this_machine=False` makes it behave as the wrong computer. The
    substitution is in the test tree (`tests/mac_shim.py`), not in the product:
    the required identity is pinned and nothing on the command path can redefine
    it. The bind itself is unit-tested for real in `test_mac_identity.py`.
    """
    stub_dir, _ = uv_stub(tmp_path)
    shim, shim_env = mac_shim(tmp_path, holds=on_this_machine)
    environment = {
        **os.environ,
        "PATH": f"{stub_dir}:{os.environ['PATH']}",
        "PYTHONPATH": f"{shim}:{REPO_ROOT}",
        **shim_env,
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


#: `07b` made the sink mandatory, so every test that expects the script to *reach*
#: the verb has to name one. Kept as one constant rather than spelled at each call
#: site: when it was spelled five times, a test could keep passing while asserting a
#: destination no operator would be told to use.
#:
#: **And that is exactly what it did.** This named `--sink replacement-host` until the
#: PR #129 review measured what that branch writes: a plain `TokenStore` in a local
#: directory, on the laptop this script verifies it is running on. The verb refuses it
#: now, so the constant names the destination an operator is actually told to use —
#: which is the property the paragraph above claimed to protect and did not.
SINK = (
    "--sink",
    "emergency-artifact",
    "--artifact",
    "/tmp/networth-test-recovery.sealed",
    "--backup-key",
    "/tmp/networth-test-backup.key",
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


def test_a_run_that_names_no_sink_is_refused_rather_than_defaulted(tmp_path: Path) -> None:
    """**Rewritten by `07b`.** This used to assert "takes the flow id and nothing
    else", which was right when the script had nothing to pass on. The sink is now a
    required choice, so the argument list is open — and the property worth keeping is
    the one underneath: the script does not *invent* a destination. A default here
    would pick which computer the owner's access_token lands on."""
    result = run(FLOW_ID, tmp_path=tmp_path)

    assert result.returncode == 2
    assert "--sink" in result.stderr
    assert not (tmp_path / "uv.argv").exists(), "the verb was reached without a sink"


def test_a_non_sandbox_environment_is_refused_rather_than_overridden(tmp_path: Path) -> None:
    """Pinned, not inherited. A rehearsal one exported variable away from Production
    is not a rehearsal, and this is the script the owner runs by hand."""
    result = run(FLOW_ID, *SINK, tmp_path=tmp_path, env={"NETWORTH_ENV": "production"})

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
    # This test builds its own environment rather than using `run()`, so it arms
    # the pre-flight shim itself; without it the check passes only on
    # `zelengs-macbook-air-2` and this is the test that goes red everywhere else.
    shim, shim_env = mac_shim(tmp_path)
    result = subprocess.run(
        ["bash", str(SCRIPT), FLOW_ID, *SINK],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "PYTHONPATH": f"{shim}:{REPO_ROOT}",
            **shim_env,
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
        # Forwarded, not re-validated. The script checks that a sink was *named*;
        # which combinations are legal is `_sink_from`'s decision, and a second
        # opinion here would meet the operator as whichever of the two is stricter.
        *SINK,
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
    result = run(FLOW_ID, *SINK, tmp_path=tmp_path, on_this_machine=False)

    assert result.returncode == 2
    # The pinned address, because a caller can no longer nominate a different one.
    assert "100.96.163.67" in result.stderr
    assert not (tmp_path / "uv.argv").exists(), "the recovery verb was reached anyway"
    assert "complete-hosted-link" not in result.stdout


def test_the_right_machine_still_reaches_the_recovery_verb(tmp_path: Path) -> None:
    """The other half, so the refusal above is a discriminator and not a wall."""
    result = run(FLOW_ID, *SINK, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    argv = (tmp_path / "uv.argv").read_text().split()
    assert "complete-hosted-link" in argv


def test_the_owner_is_told_what_the_fence_costs_him_before_he_is_asked(
    tmp_path: Path,
) -> None:
    """`07b` criterion 6's second half, asserted on what the script *prints*.

    *"State plainly in the owner-facing text that this host is also his exit node
    (§15.1), so powering it off is a real decision and not a formality."* The prompt
    itself says so too (``test_link_fence.py``), but it arrives after the script has
    already told him what the run is going to ask for — and a comment in the header is
    not owner-facing text, so this reads the transcript rather than the source.
    """
    result = run(FLOW_ID, *SINK, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "exit node" in result.stdout
    assert "VPN exit" in result.stdout
    assert "powered off" in result.stdout
    # And that nothing is presented as a check: the fence is his attestation.
    assert "nothing here pings anything" in result.stdout


def test_the_owner_is_told_the_clock_before_he_is_asked_for_anything(
    tmp_path: Path,
) -> None:
    """`07b`'s seventh criterion, asserted on the transcript for the fence's reason.

    *"The owner-facing text states **30 minutes**, and the script is pre-staged as one
    command — this is a minutes procedure, not a six-hour one."*

    The number was already in this file when this test was written — in a **source
    comment**, explaining why `--exchange` is not a choice — and `DESIGN.md` states it
    in a dozen places including §19 step 5. Neither is the owner-facing text this
    criterion is about: a comment is read by nobody, and the runbook is read *before*
    the emergency. The place rev 17's error (*"You have six hours, not thirty
    minutes"* — backwards) would still reach him is the terminal he is looking at with
    the clock already running, and until this test that terminal said nothing about it.

    The second assertion is the half that changes what he does. "30 minutes" alone
    reads as thirty minutes from now; the window opened when Link finished, which was
    before he went looking for this script, so some of it is already spent.
    """
    result = run(FLOW_ID, *SINK, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "30 minutes" in result.stdout
    assert "from when Link finished" in result.stdout
    assert "minutes procedure" in result.stdout
