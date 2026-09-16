"""What `scripts/link-complete.sh` refuses, and what it actually deletes.

The driver's value is the same shape as `link-start.sh`'s and points the other
way: the VPS exchanges, and **this Mac** retires its own copy of the recovery
record, on a positive report and never on an exit status (`DESIGN.md` §4). None
of that is observable from the script's text, so the tests run it with a stub in
place of the remote half and read what happened to the file.

**The two hosts get two directories here, always.** PR #75's first attempt at
this deleted inside the VPS verb and was tested with one directory shared between
both halves, which made a deletion the real topology could never perform look
like a passing test.

Nothing here reaches the network or exchanges anything.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.mac_shim import mac_shim

SCRIPT = REPO_ROOT / "scripts" / "link-complete.sh"
FLOW_ID = "0123456789abcdef0123456789abcdef"
OTHER_FLOW_ID = "fedcba9876543210fedcba9876543210"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=dict(os.environ),
        timeout=180,
    )


def test_the_script_parses() -> None:
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_it_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK), "the runbook tells the owner to run it directly"


@pytest.mark.parametrize("ref", ["main", "HEAD", "0" * 39, "0" * 41, "A" * 40, "", "$(id)"])
def test_only_a_full_lowercase_hex_commit_is_accepted(ref: str) -> None:
    result = run(ref, "--flow", FLOW_ID, "--link-mode", "exchange")

    assert result.returncode == 2
    assert "is not a full commit id" in result.stderr or "usage:" in result.stderr


@pytest.mark.parametrize("flow", ["", "g" * 32, FLOW_ID.upper(), FLOW_ID[:-1], "../x"])
def test_only_a_flow_id_is_accepted(flow: str) -> None:
    """The value names the file that gets deleted, so it is checked here as well as
    in the verb — `reap_expired`'s rule, applied to an argument."""
    result = run("0" * 40, "--flow", flow, "--link-mode", "exchange")

    assert result.returncode == 2
    assert "is not a flow id" in result.stderr or "--flow is required" in result.stderr


def test_the_mode_is_required_and_never_defaulted() -> None:
    """A default that exchanges is a default that spends an Item slot (F2a)."""
    result = run("0" * 40, "--flow", FLOW_ID)

    assert result.returncode == 2
    assert "never defaulted" in result.stderr


def test_an_unknown_mode_is_refused_rather_than_passed_through() -> None:
    """This half decides whether a missing marker is a failure or the expected
    ending, and it cannot decide that from a mode it does not recognise."""
    result = run("0" * 40, "--flow", FLOW_ID, "--link-mode", "exchange-thrice")

    assert result.returncode == 2
    assert "unknown --link-mode" in result.stderr


def test_an_unexpected_argument_is_refused_rather_than_ignored() -> None:
    result = run("0" * 40, "--flow", FLOW_ID, "--link-mode", "exchange", "--yes")

    assert result.returncode == 2
    assert "unexpected argument" in result.stderr


def test_a_commit_that_is_not_this_checkout_is_refused() -> None:
    result = run("0" * 40, "--flow", FLOW_ID, "--link-mode", "exchange")

    assert result.returncode == 2
    assert "this checkout is at" in result.stderr


def _throwaway_checkout(tmp_path: Path, transcript: str) -> tuple[Path, str]:
    """A real git repo holding this driver and a stub in place of the transport.

    Built rather than reusing this checkout because the driver refuses a dirty
    tree, and a test that skipped whenever anyone was working would be green for
    the wrong reason.
    """
    repo = tmp_path / "checkout"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "link-complete.sh").write_text(SCRIPT.read_text(encoding="utf-8"))
    (repo / "scripts" / "link-complete.sh").chmod(0o755)
    stub = repo / "scripts" / "sandbox-rehearsal-remote.sh"
    stub.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > " + str(tmp_path / "remote.argv") + "\n" + transcript
    )
    stub.chmod(0o755)
    for command in (
        ["git", "init", "--quiet", "-b", "main"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "test"],
        ["git", "add", "."],
        ["git", "commit", "--quiet", "-m", "fixture"],
    ):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo, sha


def _uv_stub(tmp_path: Path) -> Path:
    """`uv run --quiet networth ...` -> this interpreter's `-m networth ...`."""
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    stub = stub_dir / "uv"
    stub.write_text(
        "#!/bin/sh\n"
        '[ "$1" = run ] && shift\n'
        '[ "$1" = --quiet ] && shift\n'
        '[ "$1" = networth ] && shift\n'
        f'exec {sys.executable} -m networth "$@"\n'
    )
    stub.chmod(0o755)
    return stub_dir


def _remote(*, exchanges: str | None, exit_code: int = 0) -> str:
    """A stub transport: prints a transcript, optionally reports an exchange.

    ``exchanges`` is the flow id the *remote* claims to have completed, which is
    deliberately separable from the one the driver was asked about.
    """
    lines = [
        'echo "commit        $1"',
        'echo "verb          networth complete-hosted-link"',
        'echo "exchanged     1 public_token(s)"',
    ]
    if exchanges is not None:
        lines.append(
            f'echo \'networth-link-complete-v1:{{"flow_id":"{exchanges}","outcome":"EXCHANGED"}}\''
        )
    lines.append(f"exit {exit_code}")
    return "\n".join(lines) + "\n"


def _drive(
    tmp_path: Path,
    repo: Path,
    sha: str,
    mac_recovery: Path,
    *,
    mode: str = "exchange",
    flow: str = FLOW_ID,
    break_directory_fsync: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the driver against a Mac directory the remote half has no access to."""
    shim, shim_env = mac_shim(tmp_path, break_directory_fsync=break_directory_fsync)
    result = subprocess.run(
        [
            "bash",
            str(repo / "scripts" / "link-complete.sh"),
            sha,
            "--flow",
            flow,
            "--link-mode",
            mode,
        ],
        capture_output=True,
        text=True,
        cwd=str(repo),
        env={
            **os.environ,
            "NETWORTH_LINK_RECOVERY_DIR": str(mac_recovery),
            "PATH": f"{_uv_stub(tmp_path)}:{os.environ['PATH']}",
            "PYTHONPATH": f"{shim}:{REPO_ROOT}",
            **shim_env,
        },
        timeout=180,
    )
    return result


def _seed(mac_recovery: Path, flow_id: str = FLOW_ID) -> Path:
    """This Mac's copy of a pending flow's recovery record, on disk.

    Written as bytes rather than through the module so the fixture does not depend
    on the code path under test.
    """
    mac_recovery.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = mac_recovery / f"{flow_id}.json"
    record.write_text(
        '{"schema":"networth.link-recovery.1",'
        f'"flow_id":"{flow_id}",'
        '"link_token":"link-sandbox-synthetic",'
        '"minted_at":"2026-09-15T11:00:00+00:00",'
        '"link_token_expires_at":null,"url_lifetime_seconds":null,'
        '"reap_after":"2026-09-15T18:00:00+00:00",'
        '"second_copy_holder":"zelengs-macbook-air-2",'
        '"second_copy_verified_at":"2026-09-15T11:00:00+00:00"}\n',
        encoding="utf-8",
    )
    record.chmod(0o600)
    return record


def test_a_reported_exchange_retires_the_record_on_this_machine(tmp_path: Path) -> None:
    """The whole point, read off a run rather than off the source.

    The remote half here writes nothing and deletes nothing — it is a stub that
    prints. The file that disappears is in the Mac's directory, so the deletion
    could only have been performed by the local half of the pipeline.
    """
    repo, sha = _throwaway_checkout(tmp_path, _remote(exchanges=FLOW_ID))
    mac_recovery = tmp_path / "mac-link-recovery"
    record = _seed(mac_recovery)

    result = _drive(tmp_path, repo, sha, mac_recovery)

    assert result.returncode == 0, result.stderr
    assert not record.exists(), "the flow reported EXCHANGED, so the record is residue"
    assert "retired" in result.stdout
    # The remote half was invoked as the reviewed transport, with the mode asked for.
    argv = (tmp_path / "remote.argv").read_text(encoding="utf-8").split()
    assert argv == [
        sha,
        "--verb",
        "complete-hosted-link",
        "--flow",
        FLOW_ID,
        "--link-mode",
        "exchange",
    ]


def test_a_remote_that_reports_nothing_keeps_the_record(tmp_path: Path) -> None:
    """An exchanging mode whose transcript never said EXCHANGED. The record is the
    only way back to a flow that may still be live, so absence means keep — and the
    driver exits non-zero so the owner is not told it cleaned up."""
    repo, sha = _throwaway_checkout(tmp_path, _remote(exchanges=None))
    mac_recovery = tmp_path / "mac-link-recovery"
    record = _seed(mac_recovery)

    result = _drive(tmp_path, repo, sha, mac_recovery)

    assert result.returncode == 1
    assert record.exists()
    assert "did not retire its record" in result.stderr


def test_a_failing_remote_keeps_the_record_and_reports_which_half_failed(
    tmp_path: Path,
) -> None:
    """The two halves fail with different consequences, so PIPESTATUS is read rather
    than `$?`: a remote failure means the flow may still be live."""
    repo, sha = _throwaway_checkout(tmp_path, _remote(exchanges=None, exit_code=3))
    mac_recovery = tmp_path / "mac-link-recovery"
    record = _seed(mac_recovery)

    result = _drive(tmp_path, repo, sha, mac_recovery)

    assert result.returncode == 3, "the remote's status, not the local half's"
    assert record.exists()
    assert "the remote half exited 3" in result.stderr


def test_a_remote_that_fails_after_reporting_the_exchange_says_so(tmp_path: Path) -> None:
    """The contradiction PR #75's re-review found, end to end.

    `complete-hosted-link` prints the EXCHANGED marker *before* `--exchange-twice`
    runs its second exchange and its first-token probe, so a nonzero remote status
    can arrive after the record has already been retired — and a post-marker ssh or
    runner failure has the same shape. The first driver inferred the record's fate
    from the remote's exit status and therefore announced "this Mac kept its
    recovery record" about a file it had just deleted.

    The deletion is correct and stays: the marker is printed only after the
    exchange landed, and an exchanged flow cannot be stranded. What is asserted
    here is that the *report* matches the disk.
    """
    repo, sha = _throwaway_checkout(tmp_path, _remote(exchanges=FLOW_ID, exit_code=3))
    mac_recovery = tmp_path / "mac-link-recovery"
    record = _seed(mac_recovery)

    result = _drive(tmp_path, repo, sha, mac_recovery)

    assert result.returncode == 3, "the remote's status still reaches the caller"
    assert not record.exists(), "the exchange landed, so retiring the record was right"
    assert "AFTER reporting the exchange" in result.stderr
    assert "has no recovery record" in result.stderr
    # The claims that were false. Asserted as absences so the test fails if the
    # old wording comes back by any route — and phrased as the *advice* rather
    # than as one sentence, because the second re-review found the same false
    # advice arriving through a different sentence.
    assert "kept its recovery record" not in result.stderr
    assert "may still be live" not in result.stderr
    assert "the way back" not in result.stderr


def test_a_cleanup_fault_after_the_exchange_never_reads_as_a_live_flow(
    tmp_path: Path,
) -> None:
    """The second re-review's blocker, end to end and at the real boundary.

    Three things at once, which is the combination that produced the wrong
    sentence: a marker proved the exchange landed, the cleanup then faulted, and
    the remote exited nonzero afterwards. The old driver read a single `kept` and
    told the owner "the flow may still be live and that file is the way back" —
    for a flow whose exchange was already proven, about a record the fault had
    already unlinked, since `delete()` fsyncs the directory only after the unlink.

    Driven through the shim rather than asserted about: the child really unlinks
    and the synthetic fault really lands between that and the return, so the
    filesystem state under the report is the true one.
    """
    repo, sha = _throwaway_checkout(tmp_path, _remote(exchanges=FLOW_ID, exit_code=3))
    mac_recovery = tmp_path / "mac-link-recovery"
    record = _seed(mac_recovery)

    result = _drive(tmp_path, repo, sha, mac_recovery, break_directory_fsync=True)

    assert result.returncode == 3, "the remote's status still reaches the caller"
    assert not record.exists(), "the unlink happened before the fault; that is the case"
    assert "not removed cleanly" in result.stderr, "the fault itself is still reported"

    # What the driver must now say, and the two things it must not.
    assert "AFTER reporting the exchange" in result.stderr
    assert "nothing is stranded and nothing needs recovering" in result.stderr.lower()
    assert "may still be live" not in result.stderr, (
        "a marker proved the exchange landed; the flow is not live"
    )
    assert "the way back" not in result.stderr, (
        "there is nothing to go back to, and the record is gone in any case"
    )


def test_the_two_nonzero_endings_are_told_apart(tmp_path: Path) -> None:
    """Same exit status, opposite facts about the record, so the words must differ.

    A reader of the failure has one question — is the flow still recoverable — and
    the answer is yes in one case and "there is nothing to recover" in the other.
    """
    reports = []
    for label, exchanges in (("marker", FLOW_ID), ("silent", None)):
        # A workspace each: `_throwaway_checkout` and `_drive` both build fixtures
        # named relative to the path they are given, so sharing one would have the
        # second run reading the first one's stub transport.
        workspace = tmp_path / label
        workspace.mkdir()
        repo, sha = _throwaway_checkout(workspace, _remote(exchanges=exchanges, exit_code=3))
        mac_recovery = workspace / "mac-link-recovery"
        _seed(mac_recovery)
        reports.append(_drive(workspace, repo, sha, mac_recovery).stderr)

    assert reports[0] != reports[1]
    # Asserted on the advice rather than on a phrase. The reader's question is
    # "is there anything to recover", and the two runs must answer it opposite
    # ways — which stays true if either message is reworded, and is what the
    # previous phrase-matching version of this assertion did not pin.
    marker, silent = reports
    for claim in ("may still be live", "the way back"):
        assert claim in silent, "no marker arrived, so the flow may genuinely be live"
        assert claim not in marker, "a marker proved the exchange; there is nothing to recover"
    assert "nothing is stranded and nothing needs recovering" in marker.lower()


def test_retrieve_only_keeps_the_record_and_succeeds(tmp_path: Path) -> None:
    """Measurement (i) comes back to this flow in 30 minutes. The mode is
    non-destructive by design, so the absence of a marker is the expected ending
    rather than a fault — the one case where exit 0 and a surviving record agree."""
    repo, sha = _throwaway_checkout(tmp_path, _remote(exchanges=None))
    mac_recovery = tmp_path / "mac-link-recovery"
    record = _seed(mac_recovery)

    result = _drive(tmp_path, repo, sha, mac_recovery, mode="retrieve-only")

    assert result.returncode == 0, result.stderr
    assert record.exists()
    assert "kept" in result.stdout


def test_a_transcript_about_another_flow_deletes_nothing(tmp_path: Path) -> None:
    """Two overlapping rehearsals, end to end: the remote reports one flow and the
    driver was asked about another. Deleting here would destroy the disaster copy
    of a flow that is still live."""
    repo, sha = _throwaway_checkout(tmp_path, _remote(exchanges=OTHER_FLOW_ID))
    mac_recovery = tmp_path / "mac-link-recovery"
    record = _seed(mac_recovery)
    other = _seed(mac_recovery, OTHER_FLOW_ID)

    result = _drive(tmp_path, repo, sha, mac_recovery)

    assert result.returncode == 2
    assert record.exists(), "the flow this run was asked to retire is untouched"
    assert other.exists(), "and so is the one the transcript was about"
    assert "is about a different one" in result.stderr
