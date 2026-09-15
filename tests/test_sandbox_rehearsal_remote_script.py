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
    assert (
        argv[-1] == f"sudo -u networth -H bash -s -- {sha} --paths-only --verb rehearse-sandbox"
    ), "the remote command changed"

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


@pytest.mark.parametrize(
    "verb",
    [
        "rehearse-sandbox; id",
        "rehearse-sandbox rm -rf /",
        "$(id)",
        "backup",
        "demo",
        "REHEARSE-SANDBOX",
        "",
    ],
)
def test_a_verb_outside_the_allow_list_never_reaches_the_wire(verb: str, tmp_path: Path) -> None:
    """An allow-list, and refused on *this* side of the connection.

    The verb is interpolated into the command string handed to ssh, so it is the one
    argument here that could carry shell meaning. This project has already paid four
    review rounds establishing that the set of spellings a shell finds interesting is
    not one anybody finishes enumerating, so the check is "is it one of these three
    words" rather than "does it contain anything frightening" — and `backup` and `demo`
    are in this list precisely because they *are* real verbs of this CLI and still must
    not be runnable on the host holding the Plaid master credential.

    The stub `ssh` is deliberately absent from PATH: if the refusal did not land, the
    script would try the real one, and this test would fail by connecting rather than
    by asserting.
    """
    key = tmp_path / "key"
    key.write_text("not a real key")
    stub_dir, argv_log, _ = ssh_stub(tmp_path)

    result = run(
        FULL_SHA,
        "--verb",
        verb,
        env={
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "NETWORTH_VPS_KEY": str(key),
        },
    )

    assert result.returncode == 2
    assert "allow-list" in result.stderr
    assert not argv_log.exists(), "the refusal came after ssh was invoked"


def test_the_chosen_verb_is_what_the_host_is_asked_to_run(tmp_path: Path) -> None:
    """`probe-hosted-link` is task 06a's F7 criterion 2 and has to be reachable.

    The alternative was a second copy of the runner, which would have meant a second
    copy of the commit verification and the hash-pinned install, drifting from the
    reviewed one from its first commit.
    """
    key = tmp_path / "key"
    key.write_text("not a real key")
    stub_dir, argv_log, _ = ssh_stub(tmp_path)
    sha = head_sha()

    result = run(
        sha,
        "--verb",
        "probe-hosted-link",
        env={
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "NETWORTH_VPS_KEY": str(key),
            "NETWORTH_VPS_TARGET": "root@198.51.100.1",
        },
    )

    assert result.returncode == 0, result.stderr
    argv = argv_log.read_text().splitlines()
    assert argv[-1] == f"sudo -u networth -H bash -s -- {sha}  --verb probe-hosted-link"


def test_print_url_is_not_a_thing_this_transport_can_forward(tmp_path: Path) -> None:
    """The hosted URL is openable by whoever holds it, and a transcript outlives a run.

    Nothing can print it any more — `--print-url` was removed from the verb itself in
    PR #59's review — so this is now the *second* of two independent refusals rather
    than the only one. It is kept because the owner-run half of `06a` will need the URL
    on his screen, and when it arrives it will arrive as a change to the verb; this
    test is what makes widening the transport a separate, visible decision instead of
    something that comes along for the ride.
    """
    key = tmp_path / "key"
    key.write_text("not a real key")
    stub_dir, argv_log, _ = ssh_stub(tmp_path)

    result = run(
        head_sha(),
        "--print-url",
        env={
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "NETWORTH_VPS_KEY": str(key),
        },
    )

    assert result.returncode == 2
    assert "unknown argument '--print-url'" in result.stderr
    assert not argv_log.exists()


def test_start_hosted_link_is_reachable_and_no_verb_gained_a_url_flag(
    tmp_path: Path,
) -> None:
    """The widening, pinned: one allow-list entry, and no new flag anywhere.

    `start-hosted-link` is task 06a's owner-run half, so the transport has to carry
    it. What must *not* have come with it is a way to make any verb print a URL —
    the refusal this script's header describes is an absence of options, and an
    absence is exactly what stops being checked once the headline case is allowed
    through.

    **Since PR #75 no verb reachable here prints a URL at all**: `start-hosted-link`
    emits a marked payload that `scripts/link-start.sh` pipes into
    `absorb-hosted-link`, which is where the URL is shown. So this test pins the
    absence of the flag, and `test_link_start_script.py` pins who does the showing.
    """
    key = tmp_path / "key"
    key.write_text("not a real key")
    stub_dir, argv_log, _ = ssh_stub(tmp_path)
    sha = head_sha()

    result = run(
        sha,
        "--verb",
        "start-hosted-link",
        env={
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "NETWORTH_VPS_KEY": str(key),
            "NETWORTH_VPS_TARGET": "root@198.51.100.1",
        },
    )

    assert result.returncode == 0, result.stderr
    argv = argv_log.read_text().splitlines()
    assert argv[-1] == f"sudo -u networth -H bash -s -- {sha}  --verb start-hosted-link"
    # The allow-list grew by one word and gained no option. `--print-url` was
    # removed from the verb in PR #59 and must not return through the transport.
    script = Path("scripts/sandbox-rehearsal-remote.sh").read_text(encoding="utf-8")
    assert "--print-url" not in script


def test_complete_hosted_link_reaches_the_host_with_both_of_its_arguments(
    tmp_path: Path,
) -> None:
    """The other half of 06a, and the reason this transport grew two options.

    Until this existed the verb was on neither allow-list, deliberately: it takes
    arguments and nothing here forwarded any, so listing it would have made it
    reachable and always broken. The argument vector is read back from the stub
    rather than inferred from the script's text — the mode decides whether a
    `public_token` is spent, so "the script mentions --link-mode somewhere" is not
    the property worth asserting.
    """
    key = tmp_path / "key"
    key.write_text("not a real key")
    stub_dir, argv_log, _ = ssh_stub(tmp_path)
    sha = head_sha()
    flow = "0123456789abcdef0123456789abcdef"

    result = run(
        sha,
        "--verb",
        "complete-hosted-link",
        "--flow",
        flow,
        "--link-mode",
        "retrieve-only",
        env={
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "NETWORTH_VPS_KEY": str(key),
            "NETWORTH_VPS_TARGET": "root@198.51.100.1",
        },
    )

    assert result.returncode == 0, result.stderr
    argv = argv_log.read_text().splitlines()
    assert argv[-1] == (
        f"sudo -u networth -H bash -s -- {sha}  --verb complete-hosted-link "
        f"--flow {flow} --link-mode retrieve-only"
    )
    # The mode is in the transcript, because the transcript outlives the run and
    # `--exchange` and `--retrieve-only` differ by a spent lifetime slot.
    assert "--retrieve-only" in result.stdout


@pytest.mark.parametrize(
    ("flow", "why"),
    [
        ("0123456789ABCDEF0123456789abcdef", "uppercase is not how new_flow_id spells it"),
        ("0123456789abcdef0123456789abcde", "31 characters"),
        ("0123456789abcdef0123456789abcdef0", "33 characters"),
        ("0123456789abcdef0123456789abcde;", "a separator the shell would read"),
        ("$(id)", "a substitution"),
        ("../../etc/networth", "a path"),
    ],
)
def test_a_flow_id_that_is_not_32_hex_characters_never_reaches_the_wire(
    flow: str, why: str, tmp_path: Path
) -> None:
    """Checked against the grammar of a `flow_id`, not against a list of frightening
    spellings — `uuid.uuid4().hex` is 32 lowercase hex characters and nothing else.

    The `ssh` stub is absent from PATH so a refusal that did not land would fail this
    test by connecting.
    """
    key = tmp_path / "key"
    key.write_text("not a real key")
    stub_dir, argv_log, _ = ssh_stub(tmp_path)

    result = run(
        FULL_SHA,
        "--verb",
        "complete-hosted-link",
        "--flow",
        flow,
        "--link-mode",
        "exchange",
        env={
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "NETWORTH_VPS_KEY": str(key),
        },
    )

    assert result.returncode == 2, why
    assert "flow id" in result.stderr
    assert not argv_log.exists(), "the refusal came after ssh was invoked"


@pytest.mark.parametrize(
    "link_mode",
    ["--exchange", "exchange; id", "EXCHANGE", "retrieve", "", "exchange twice"],
)
def test_a_link_mode_outside_the_allow_list_never_reaches_the_wire(
    link_mode: str, tmp_path: Path
) -> None:
    """Three plain words, and the flag is built from the one that matched.

    `--exchange` is in this list on purpose: the allow-list holds words, so the
    spelling that is *already* an option is not one of them. That is what keeps the
    thing interpolated into the remote command from being an option nobody listed.
    """
    key = tmp_path / "key"
    key.write_text("not a real key")
    stub_dir, argv_log, _ = ssh_stub(tmp_path)

    result = run(
        FULL_SHA,
        "--verb",
        "complete-hosted-link",
        "--flow",
        "0123456789abcdef0123456789abcdef",
        "--link-mode",
        link_mode,
        env={
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "NETWORTH_VPS_KEY": str(key),
        },
    )

    assert result.returncode == 2
    assert "link mode" in result.stderr or "--link-mode needs a name" in result.stderr
    assert not argv_log.exists(), "the refusal came after ssh was invoked"


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("--verb", "complete-hosted-link", "--link-mode", "exchange"), "needs --flow"),
        (
            ("--verb", "complete-hosted-link", "--flow", "0" * 32),
            "needs --link-mode",
        ),
        (
            (
                "--paths-only",
                "--verb",
                "complete-hosted-link",
                "--flow",
                "0" * 32,
                "--link-mode",
                "exchange",
            ),
            "no --paths-only form",
        ),
        (("--flow", "0" * 32, "--link-mode", "exchange"), "only to complete-hosted-link"),
        (("--verb", "start-hosted-link", "--flow", "0" * 32), "only to complete-hosted-link"),
    ],
)
def test_the_couplings_are_refused_before_a_connection_is_opened(
    args: tuple[str, ...], expected: str, tmp_path: Path
) -> None:
    """Each of these otherwise fails on the far side of a hash-pinned install.

    A caller who omits the mode would watch a venv get built before argparse told
    him the run was never going to work, and one who passes `--flow` to a verb that
    ignores it would get a *successful* run that measured nothing. Both are refused
    on this Mac instead, which is the same placement as the verb allow-list and for
    the same reason.
    """
    key = tmp_path / "key"
    key.write_text("not a real key")
    stub_dir, argv_log, _ = ssh_stub(tmp_path)

    result = run(
        FULL_SHA,
        *args,
        env={
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "NETWORTH_VPS_KEY": str(key),
        },
    )

    assert result.returncode == 2
    assert expected in result.stderr
    assert not argv_log.exists(), "the refusal came after ssh was invoked"


# ---------------------------------------------------------------------------
# Where the minted token is allowed to land.
#
# `start-hosted-link` writes a `link_token` to stdout. The verb itself refuses a
# terminal, but it cannot refuse a file: it runs on the VPS, and sshd gives it a
# pipe whether the Mac end of that pipe is `networth absorb-hosted-link` or
# `> mint.log`. The destination is only observable on this side, so the check is
# on this side, and these tests read it from the side that can see it.
#
# The discriminator is measured, not assumed: `| cat` makes fd 1 a FIFO, a
# redirect makes it a regular file, and `> /dev/null` makes it a character
# device. Requiring the FIFO admits the supported caller and nothing else.
# ---------------------------------------------------------------------------


def run_with_stdout(
    *args: str, stdout: object, tmp_path: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the script with stdout pointed somewhere specific.

    The module's `run()` uses `capture_output=True`, which makes fd 1 a pipe — the
    supported case. These tests need the unsupported ones, which means choosing the
    destination rather than letting subprocess choose it.
    """
    environment = dict(os.environ)
    environment.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        stdout=stdout,  # type: ignore[arg-type]
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
        env=environment,
        timeout=120,
    )


def mint_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    key = tmp_path / "key"
    key.write_text("not a real key")
    stub_dir, argv_log, _ = ssh_stub(tmp_path)
    return {
        "PATH": f"{stub_dir}:{os.environ['PATH']}",
        "NETWORTH_VPS_KEY": str(key),
        "NETWORTH_VPS_TARGET": "root@198.51.100.1",
    }, argv_log


def test_a_mint_redirected_into_a_regular_file_refuses_before_ssh_is_reached(
    tmp_path: Path,
) -> None:
    """The blocker this check exists for: `... --verb start-hosted-link > mint.log`.

    Refused *before* the connection, so the refusal is not "we minted a token and
    then declined to show it" — nothing is minted, and by F2a nothing can be spent
    through a token that does not exist. The proof of that ordering is that the ssh
    stub was never invoked: it records its argv on every call, so the absence of the
    log file is the absence of the call.
    """
    env, argv_log = mint_env(tmp_path)
    transcript = tmp_path / "mint.log"

    with transcript.open("w") as handle:
        result = run_with_stdout(
            head_sha(),
            "--verb",
            "start-hosted-link",
            stdout=handle,
            tmp_path=tmp_path,
            env=env,
        )

    assert result.returncode == 2
    assert "not a pipe" in result.stderr
    assert not argv_log.exists(), "ssh was reached; the refusal came too late to matter"
    # Not even the header lines reached the file. A partial transcript would mean
    # the check sits below the printfs, which is a different guarantee.
    assert transcript.read_text() == ""


def test_a_mint_discarded_to_dev_null_is_refused_too(tmp_path: Path) -> None:
    """`/dev/null` is not a file, and the rule is still "the pipe or nothing".

    Worth pinning separately because it is the case a "no regular files" rule would
    let through, and it is not harmless: it mints a live token on the VPS and throws
    away the only copy of it, which is the crash state measurement (i) exists to
    bound rather than a way to run the verb safely.
    """
    env, argv_log = mint_env(tmp_path)

    with open(os.devnull, "w") as handle:
        result = run_with_stdout(
            head_sha(),
            "--verb",
            "start-hosted-link",
            stdout=handle,
            tmp_path=tmp_path,
            env=env,
        )

    assert result.returncode == 2
    assert "not a pipe" in result.stderr
    assert not argv_log.exists()


def test_the_supported_pipe_still_reaches_the_host(tmp_path: Path) -> None:
    """`scripts/link-start.sh`'s shape, kept green.

    This is the other half of the check and the one that makes it a discriminator
    rather than a blanket refusal: the same command whose only difference is that
    fd 1 is a FIFO goes through, with the mint verb intact on the wire.
    """
    env, argv_log = mint_env(tmp_path)
    sha = head_sha()

    result = run_with_stdout(
        sha,
        "--verb",
        "start-hosted-link",
        stdout=subprocess.PIPE,
        tmp_path=tmp_path,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    argv = argv_log.read_text().splitlines()
    assert argv[-1] == f"sudo -u networth -H bash -s -- {sha}  --verb start-hosted-link"


def test_paths_only_is_exempt_because_it_returns_before_the_mint(tmp_path: Path) -> None:
    """The exemption, pinned so it is a decision rather than an oversight.

    `--paths-only` prints which credential and item files the environment selects
    and returns before any Plaid call, so its stdout carries no token and redirecting
    it to a file is how anyone would reasonably read it.
    """
    env, argv_log = mint_env(tmp_path)
    sha = head_sha()
    listing = tmp_path / "paths.txt"

    with listing.open("w") as handle:
        result = run_with_stdout(
            sha,
            "--verb",
            "start-hosted-link",
            "--paths-only",
            stdout=handle,
            tmp_path=tmp_path,
            env=env,
        )

    assert result.returncode == 0, result.stderr
    argv = argv_log.read_text().splitlines()
    assert argv[-1] == (
        f"sudo -u networth -H bash -s -- {sha} --paths-only --verb start-hosted-link"
    )
