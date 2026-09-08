"""What `scripts/sandbox-rehearsal-remote.sh` sends, and what it refuses to send.

This script exists because the step it replaces was shell in a PR body that uploaded
the runner with ``cat > /tmp/sandbox-rehearsal.sh`` as root — a predictable path an
unprivileged local process can pre-create as a symlink, so root's redirection writes
through it. Codex reproduced that primitive in the PR #49 pre-execution review.

The fix is that **no file is created on either machine**, and the point of this suite
is that the claim is checked rather than asserted: a stub ``ssh`` on ``PATH`` records
the arguments and the stdin it was handed, so the tests read what would have gone over
the wire. Nothing here reaches the network.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "sandbox-rehearsal-remote.sh"
FULL_SHA = "0" * 40


def ssh_stub(tmp_path: Path) -> tuple[Path, Path, Path]:
    """An ``ssh`` that records its argv and its stdin instead of connecting.

    argv is recorded **one argument per line**. It used to be space-joined, and
    that lost the only boundary that matters here: which argument a path came
    from. See the assertion in the piping test for what that cost.
    """
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir(exist_ok=True)
    argv_log = tmp_path / "ssh.argv"
    stdin_log = tmp_path / "ssh.stdin"
    stub = stub_dir / "ssh"
    stub.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {argv_log}\ncat > {stdin_log}\nexit 0\n")
    stub.chmod(0o755)
    return stub_dir, argv_log, stdin_log


def run(
    *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd or REPO_ROOT),
        env=environment,
        timeout=120,
    )


def head_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_the_script_parses() -> None:
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_it_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)


@pytest.mark.parametrize("ref", ["main", "HEAD", "0" * 39, "ABCDEF" + "0" * 34, ""])
def test_only_a_full_lowercase_hex_commit_is_accepted(ref: str, tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_text("not a real key")

    result = run(ref, env={"NETWORTH_VPS_KEY": str(key)})

    assert result.returncode == 2
    assert "commit" in result.stderr


def test_an_unreadable_identity_is_refused_rather_than_falling_back_to_a_bare_ssh(
    tmp_path: Path,
) -> None:
    """This Mac has no default SSH identity, so there is no fallback to fall back to.

    A bare `ssh` here does not "use the default key", it fails on publickey — which is
    why every hop in this project carries `-i` and why the absence of one is a refusal
    rather than a warning.
    """
    result = run(FULL_SHA, env={"NETWORTH_VPS_KEY": str(tmp_path / "absent")})

    assert result.returncode == 2
    assert "is not readable" in result.stderr


def test_a_commit_this_checkout_does_not_have_is_refused(tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_text("not a real key")

    result = run("0" * 40, env={"NETWORTH_VPS_KEY": str(key)})

    assert result.returncode == 2
    assert "is not in this checkout" in result.stderr


def test_the_runner_is_piped_to_the_service_user_and_no_file_is_written(
    tmp_path: Path,
) -> None:
    """The whole fix for the symlink blocker, read off the wire.

    `bash -s` takes the program from stdin, so there is no pathname on the host for
    anything to have pre-created.
    """
    key = tmp_path / "key"
    key.write_text("not a real key")
    stub_dir, argv_log, stdin_log = ssh_stub(tmp_path)
    sha = head_sha()

    result = run(
        sha,
        "--paths-only",
        env={
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "NETWORTH_VPS_KEY": str(key),
            "NETWORTH_VPS_TARGET": "root@198.51.100.1",
        },
    )

    assert result.returncode == 0, result.stderr
    argv = argv_log.read_text().splitlines()

    # Checked as the exact argument vector, in two halves, rather than by
    # searching the joined command line for suspicious spellings.
    #
    # The first version of this assertion did the latter — it rejected "/tmp/"
    # anywhere in argv, meaning to catch a remote upload path. But `-i <key>` is
    # a *local* path, and Linux pytest puts `tmp_path` under /tmp, so it failed
    # in CI on the test's own scratch key while passing on macOS, where the
    # same directory is spelled differently. A scan over a joined command line
    # cannot tell which side of the connection a path belongs to; splitting at
    # the destination can, because ssh's remote command is the argument after
    # it. Equality is also the stronger check: "no file is written on the host"
    # stops being a blocklist of spellings (`cat >`, `mktemp`, `chmod`) and
    # becomes the remote command containing no pathname at all.
    assert argv[:-1] == [
        "-i",
        str(key),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "root@198.51.100.1",
    ], "the local transport options changed"
    assert argv[-1] == f"sudo -u networth -H bash -s -- {sha} --paths-only", (
        "the remote command changed"
    )

    # And the bytes on stdin are the reviewed commit's runner, not the working tree's.
    expected = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{sha}:scripts/sandbox-rehearsal.sh"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert stdin_log.read_text() == expected


def test_the_bytes_sent_come_from_the_commit_and_not_from_the_working_tree(
    tmp_path: Path,
) -> None:
    """An uncommitted local edit must not be able to ride along to the credential host.

    Written as a real edit to a scratch clone rather than as a claim about `git show`,
    because "we use git show" is exactly the kind of sentence that stays true in a diff
    that stops being true in practice.
    """
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(REPO_ROOT), str(clone)], check=True
    )
    sha = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    runner = clone / "scripts" / "sandbox-rehearsal.sh"
    runner.write_text(runner.read_text() + "\n# an uncommitted edit\n")

    key = tmp_path / "key"
    key.write_text("not a real key")
    stub_dir, _, stdin_log = ssh_stub(tmp_path)

    result = run(
        sha,
        cwd=clone,
        env={
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "NETWORTH_VPS_KEY": str(key),
            "NETWORTH_VPS_TARGET": "root@198.51.100.1",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "an uncommitted edit" not in stdin_log.read_text()


# There is deliberately no text scan for `scp`/`cat >`/`mktemp` here. The first draft
# had one and it failed on the script's own comments, which explain why those commands
# are absent. A scan cannot tell an instruction from the paragraph documenting its
# absence; the two tests above read what was actually handed to `ssh`, which can.
