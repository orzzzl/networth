"""What `scripts/link-start.sh` refuses, and what it actually pipes into what.

The driver's whole value is an **ordering across two machines**: the VPS mints,
this Mac writes and reads back the recovery record, and only then is a URL shown
(`DESIGN.md` §4, §19 step 2a). None of that is observable from the script's text,
so the tests here run it with a stub in place of the remote half and read what it
did — the same technique, and for the same reason, as
`test_sandbox_rehearsal_remote_script.py`.

Nothing here reaches the network or mints anything.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.mac_shim import mac_shim

SCRIPT = REPO_ROOT / "scripts" / "link-start.sh"


def head_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=environment,
        timeout=180,
    )


# The driver refuses to run anywhere but the Mac that holds the second copy, and a
# CI runner is not that machine. The pre-flight is therefore substituted in the
# child through `sitecustomize` (see `tests/mac_shim.py`) rather than by making the
# product read its required identity from the environment — that was the bypass
# this PR was sent back for, because it left a way to redefine which machine is
# accepted on the command the owner runs. The decision being substituted is
# unit-tested for real, in both directions, in `test_mac_identity.py`.


def test_the_script_parses() -> None:
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_it_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK), "the runbook tells the owner to run it directly"


@pytest.mark.parametrize(
    "ref",
    ["main", "HEAD", "0" * 39, "0" * 41, "A" * 40, "", "$(id)"],
)
def test_only_a_full_lowercase_hex_commit_is_accepted(ref: str) -> None:
    """The same grammar as the transport's, checked before anything runs.

    A ref that can move is not the thing that was reviewed, and this driver claims
    both halves ran the same bytes."""
    result = run(ref)

    assert result.returncode == 2
    assert "is not a full commit id" in result.stderr or "usage:" in result.stderr


def test_a_second_argument_is_refused_rather_than_ignored() -> None:
    """There is no second thing to pass. A driver that silently dropped an argument
    would let `--link-mode exchange` look accepted on the one path where a dropped
    option means a spent slot."""
    result = run("0" * 40, "--link-mode")

    assert result.returncode == 2
    assert "takes the commit and nothing else" in result.stderr


def test_a_commit_that_is_not_this_checkout_is_refused() -> None:
    """The remote half runs the commit; the local half runs this tree. If those
    differ the transcript names a commit that describes only half of what ran."""
    result = run("0" * 40)

    assert result.returncode == 2
    assert "this checkout is at" in result.stderr


def _throwaway_checkout(tmp_path: Path, transcript: str) -> tuple[Path, str]:
    """A real git repo holding this driver and a stub in place of the transport.

    Built rather than reusing this checkout for one reason: the driver refuses a
    dirty tree, so a test that ran here would **skip whenever anyone was working**
    — a test that is green because it did not run is the failure mode this project
    keeps finding in other people's evidence.
    """
    repo = tmp_path / "checkout"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "link-start.sh").write_text(SCRIPT.read_text(encoding="utf-8"))
    (repo / "scripts" / "link-start.sh").chmod(0o755)
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
    """`uv run --quiet networth ...` -> this interpreter's `-m networth ...`.

    The driver's dependency on uv is not what is under test; the pipeline is. This
    keeps the run hermetic and makes the absorbing half the real module."""
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    stub = stub_dir / "uv"
    stub.write_text(
        "#!/bin/sh\n"
        "# consume `run [--quiet] networth`, then exec the module\n"
        '[ "$1" = run ] && shift\n'
        '[ "$1" = --quiet ] && shift\n'
        '[ "$1" = networth ] && shift\n'
        f'exec {sys.executable} -m networth "$@"\n'
    )
    stub.chmod(0o755)
    return stub_dir


PAYLOAD = (
    '{"flow_id":"0123456789abcdef0123456789abcdef",'
    '"hosted_link_url":"https://sandbox.plaid.example/hosted/synthetic",'
    '"link_token":"link-sandbox-synthetic","link_token_expires_at":null,'
    '"minted_at":"2026-09-15T11:00:00+00:00","url_lifetime_seconds":null}'
)
# Single-quoted in the stub: the payload is JSON and is full of double quotes,
# which a double-quoted `echo` would end the string on.
MINT_TRANSCRIPT = (
    'echo "commit        $1"\n'
    'echo "verb          networth start-hosted-link"\n'
    f"echo 'networth-link-mint-v1:{PAYLOAD}'\n"
)


def test_the_url_is_printed_by_the_absorbing_half_after_the_record_exists(
    tmp_path: Path,
) -> None:
    """The ordering, read off a run rather than off the source.

    If the driver piped the halves the wrong way round, captured the token into a
    variable, or echoed the mint half's stdout itself, the URL would appear without
    a record on disk. The file is the witness.
    """
    repo, sha = _throwaway_checkout(tmp_path, MINT_TRANSCRIPT)
    recovery = tmp_path / "link-recovery"

    shim, shim_env = mac_shim(tmp_path)
    result = subprocess.run(
        ["bash", str(repo / "scripts" / "link-start.sh"), sha],
        capture_output=True,
        text=True,
        cwd=str(repo),
        env={
            **os.environ,
            "NETWORTH_LINK_RECOVERY_DIR": str(recovery),
            "PATH": f"{_uv_stub(tmp_path)}:{os.environ['PATH']}",
            "PYTHONPATH": f"{shim}:{REPO_ROOT}",
            **shim_env,
        },
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    record = recovery / "0123456789abcdef0123456789abcdef.json"
    assert record.exists(), "the URL may not be shown before this file does"
    assert "https://sandbox.plaid.example/hosted/synthetic" in result.stdout
    assert result.stdout.index("second copy") < result.stdout.index("hosted/synthetic")
    # The token crossed the pipe and stopped there.
    assert "link-sandbox-synthetic" not in result.stdout
    assert "link-sandbox-synthetic" not in result.stderr
    assert "networth-link-mint-v1:" not in result.stdout
    # ...and the transcript the owner is watching survived.
    assert "verb          networth start-hosted-link" in result.stdout
    # The mint half was asked for the mint and nothing else.
    assert (tmp_path / "remote.argv").read_text().split() == [
        sha,
        "--verb",
        "start-hosted-link",
    ]


def test_a_mint_this_mac_cannot_record_prints_no_url(tmp_path: Path) -> None:
    """The refusal §4 is for. The mint succeeded; the copy did not land.

    Pointing the recovery directory at a path that cannot be created is the cheapest
    true version of that failure, and the assertion is on the *absence* of the URL —
    which is the thing that would spend a slot.
    """
    repo, sha = _throwaway_checkout(tmp_path, MINT_TRANSCRIPT)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")

    shim, shim_env = mac_shim(tmp_path)
    result = subprocess.run(
        ["bash", str(repo / "scripts" / "link-start.sh"), sha],
        capture_output=True,
        text=True,
        cwd=str(repo),
        env={
            **os.environ,
            "NETWORTH_LINK_RECOVERY_DIR": str(blocker / "under-a-file"),
            "PATH": f"{_uv_stub(tmp_path)}:{os.environ['PATH']}",
            "PYTHONPATH": f"{shim}:{REPO_ROOT}",
            **shim_env,
        },
        timeout=180,
    )

    assert result.returncode != 0
    assert "hosted/synthetic" not in result.stdout
    assert "this Mac did not record it" in result.stderr
    assert "link-sandbox-synthetic" not in result.stdout + result.stderr


def test_a_mint_half_that_fails_is_reported_as_the_mint_half(tmp_path: Path) -> None:
    repo, sha = _throwaway_checkout(tmp_path, 'echo "mint refused" >&2\nexit 2\n')

    shim, shim_env = mac_shim(tmp_path)
    result = subprocess.run(
        ["bash", str(repo / "scripts" / "link-start.sh"), sha],
        capture_output=True,
        text=True,
        cwd=str(repo),
        env={
            **os.environ,
            "NETWORTH_LINK_RECOVERY_DIR": str(tmp_path / "link-recovery"),
            "PATH": f"{_uv_stub(tmp_path)}:{os.environ['PATH']}",
            "PYTHONPATH": f"{shim}:{REPO_ROOT}",
            **shim_env,
        },
        timeout=180,
    )

    assert result.returncode == 2
    assert "the mint half exited 2" in result.stderr
    assert "hosted/synthetic" not in result.stdout


def test_the_driver_names_which_half_failed() -> None:
    """The two failures have different consequences and the owner acts differently.

    A remote failure means nothing was minted. A local one means a link token exists
    on the VPS that this Mac did not record — which is the state §4 exists to make
    impossible, so it cannot be reported as the same thing.
    """
    source = SCRIPT.read_text(encoding="utf-8")

    assert "PIPESTATUS" in source, "a single $? cannot tell the two halves apart"
    assert "the mint half exited" in source
    assert "this Mac did not record it" in source
    # `set -e` around a pipeline would exit before either message could be chosen.
    assert "set +e" in source


def test_the_wrong_machine_is_refused_before_anything_is_minted(tmp_path: Path) -> None:
    """Refused one step earlier than the absorber would have refused it.

    The absorbing half checks the same thing before it writes the record, and that
    is the check that protects the record's meaning. But by the time it runs, a live
    `link_token` exists on the VPS. Asking here costs nothing and refuses while there
    is still nothing to refuse — by F2a no slot can be spent through a token that was
    never minted.

    The witness is the transport stub's argv file: it is written on every call, so
    its absence is the absence of the mint.
    """
    repo, sha = _throwaway_checkout(tmp_path, MINT_TRANSCRIPT)
    recovery = tmp_path / "link-recovery"

    shim, shim_env = mac_shim(tmp_path, holds=False)
    result = subprocess.run(
        ["bash", str(repo / "scripts" / "link-start.sh"), sha],
        capture_output=True,
        text=True,
        cwd=str(repo),
        env={
            **os.environ,
            "NETWORTH_LINK_RECOVERY_DIR": str(recovery),
            "PATH": f"{_uv_stub(tmp_path)}:{os.environ['PATH']}",
            "PYTHONPATH": f"{shim}:{REPO_ROOT}",
            **shim_env,
        },
        timeout=180,
    )

    assert result.returncode != 0
    # The refusal names the address the design pinned, not one a test chose —
    # there is no longer any way for a caller to choose one.
    assert "100.96.163.67" in result.stderr
    assert not (tmp_path / "remote.argv").exists(), "the mint half ran anyway"
    assert not recovery.exists() or list(recovery.glob("*.json")) == []
    assert "hosted/synthetic" not in result.stdout
