"""What `scripts/sandbox-rehearsal.sh` may and may not do on the sync host.

Task 06 runs on `tokyo-exit` because the Sandbox credential lives there and is
never copied. That makes this script the one artefact of the task that executes
with the service user's access, so its refusals are worth more than its happy
path — and its refusals are all reachable offline, which is why they are tested
here rather than described in a PR.

**These are behaviour tests, not shape checks, wherever behaviour is reachable.**
The guards run for real. The venv teardown runs for real, against a stub
``python3`` on ``PATH`` — the property being proved is that a *failure* in the
middle still removes the directory, and a text scan for the word ``trap`` cannot
tell that from a script that traps and then `exec`s away from its own trap. (It
did, in the first draft.)

Nothing here reaches the network, installs anything, or contacts Plaid.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "sandbox-rehearsal.sh"
FULL_SHA = "0" * 40


def run(
    *args: str, env: dict[str, str] | None = None, tmpdir: Path | None = None
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("NETWORTH_ENV", None)
    if tmpdir is not None:
        environment["TMPDIR"] = str(tmpdir)
    environment.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
    )


def stub_path(tmp_path: Path, python3: str) -> Path:
    """A directory holding one fake ``python3``, to put first on ``PATH``."""
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    stub = stub_dir / "python3"
    stub.write_text(python3)
    stub.chmod(0o755)
    return stub_dir


def test_the_script_parses() -> None:
    """A syntax error in the artefact that runs on the credential host."""
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_it_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)


@pytest.mark.parametrize(
    "ref",
    [
        "main",
        "HEAD",
        "v1.0",
        "0" * 39,
        "0" * 41,
        "0" * 39 + "g",
        "ABCDEF" + "0" * 34,
        "origin/main",
        "",
    ],
)
def test_only_a_full_lowercase_hex_commit_is_accepted(ref: str) -> None:
    """A branch moves; a commit is what was reviewed. Uppercase is not a hex id
    this project ever hands out, and a short id is ambiguous."""
    result = run(ref)

    assert result.returncode == 2
    assert "commit" in result.stderr


def test_production_is_refused_before_anything_is_installed(tmp_path: Path) -> None:
    """F2a: the refusal has to land before a venv, not inside a loaded process."""
    result = run(FULL_SHA, env={"NETWORTH_ENV": "production"}, tmpdir=tmp_path)

    assert result.returncode == 2
    assert "runs against sandbox and nothing else" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_an_unrecognised_second_argument_is_refused(tmp_path: Path) -> None:
    result = run(FULL_SHA, "--yes-really", tmpdir=tmp_path)

    assert result.returncode == 2
    assert "--paths-only" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_a_missing_credential_stops_the_run_and_names_who_installs_it(tmp_path: Path) -> None:
    """The credential is read where the owner installed it, never from a copy."""
    result = run(FULL_SHA, tmpdir=tmp_path)

    if Path("/etc/networth/plaid-sandbox.env").is_file():  # pragma: no cover - the host
        pytest.skip("this machine has the real credential installed")
    assert result.returncode == 2
    assert "/etc/networth/plaid-sandbox.env is not readable" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_the_venv_is_removed_when_the_run_fails_before_it_finishes(tmp_path: Path) -> None:
    """The whole promise of the runner: nothing it builds outlives it.

    The stub creates the directory `venv` would have created and then exits
    non-zero, so the script fails after `mktemp -d` and before the verb runs —
    the window in which a leaked venv would sit in the service user's TMPDIR.
    """
    stub = stub_path(
        tmp_path,
        '#!/bin/sh\nmkdir -p "$3/bin"\ntouch "$3/bin/pip"\nexit 1\n',
    )
    workdir = tmp_path / "tmp"
    workdir.mkdir()

    result = run(
        FULL_SHA,
        "--paths-only",
        env={"PATH": f"{stub}:{os.environ['PATH']}"},
        tmpdir=workdir,
    )

    assert result.returncode != 0
    assert list(workdir.iterdir()) == [], "the venv outlived the run that promised to remove it"


def working_venv_stub(tmp_path: Path, *, reports: str, verb_exit: int) -> Path:
    """A ``python3`` whose ``-m venv`` produces a venv that behaves as told.

    ``reports`` is the commit the installed tree claims to be; ``verb_exit`` is
    what ``networth rehearse-sandbox`` exits with. Everything the real script
    does between those two points is then exercised for real, offline.
    """
    return stub_path(
        tmp_path,
        "#!/bin/sh\n"
        'if [ "$1" != "-m" ]; then exit 1; fi\n'
        'mkdir -p "$3/bin"\n'
        'printf "#!/bin/sh\\nexit 0\\n" > "$3/bin/pip"\n'
        f'printf "#!/bin/sh\\nexit {verb_exit}\\n" > "$3/bin/networth"\n'
        f'printf "#!/bin/sh\\ncat >/dev/null\\necho {reports}\\n" > "$3/bin/python"\n'
        'chmod 755 "$3/bin/pip" "$3/bin/networth" "$3/bin/python"\n',
    )


def test_the_venv_is_removed_when_the_verb_itself_fails(tmp_path: Path) -> None:
    """The other half: a clean install whose *run* exits non-zero.

    The exit code is passed through rather than swallowed — a failed rehearsal
    that reports success is the shape of defect this project keeps finding.
    """
    stub = working_venv_stub(tmp_path, reports=FULL_SHA, verb_exit=9)
    workdir = tmp_path / "tmp"
    workdir.mkdir()

    result = run(
        FULL_SHA,
        "--paths-only",
        env={"PATH": f"{stub}:{os.environ['PATH']}"},
        tmpdir=workdir,
    )

    assert result.returncode == 9, result.stderr
    assert list(workdir.iterdir()) == []


def test_the_installed_commit_is_verified_rather_than_trusted(tmp_path: Path) -> None:
    """`pip` resolving something else must stop the run, not be assumed away."""
    stub = working_venv_stub(tmp_path, reports="deadbeef", verb_exit=0)
    workdir = tmp_path / "tmp"
    workdir.mkdir()

    result = run(
        FULL_SHA,
        "--paths-only",
        env={"PATH": f"{stub}:{os.environ['PATH']}"},
        tmpdir=workdir,
    )

    assert result.returncode == 2
    assert "records commit 'deadbeef'" in result.stderr
    assert list(workdir.iterdir()) == []


def test_a_verified_install_runs_the_verb_and_leaves_nothing_behind(tmp_path: Path) -> None:
    stub = working_venv_stub(tmp_path, reports=FULL_SHA, verb_exit=0)
    workdir = tmp_path / "tmp"
    workdir.mkdir()

    result = run(
        FULL_SHA,
        "--paths-only",
        env={"PATH": f"{stub}:{os.environ['PATH']}"},
        tmpdir=workdir,
    )

    assert result.returncode == 0, result.stderr
    assert f"installed     verified at {FULL_SHA}" in result.stdout
    assert list(workdir.iterdir()) == []


def test_the_script_never_reads_the_credential_itself(tmp_path: Path) -> None:
    """It tests that the file is *readable* and then hands it to the program.

    The credential is read by `networth` inside the venv, from the path the
    owner installed (AGENTS.md rule 1). A script that opened it — to check a
    key, to pass a value, to "just check it" — would be a second reader on the host,
    and the one whose output is a transcript.
    """
    source = SCRIPT.read_text()

    assert "PLAID_SECRET" not in source
    assert "PLAID_CLIENT_ID" not in source
    assert "ins_" not in source
    for reader in ("cat ", "source ", "grep ", "awk ", "sed ", ". "):
        assert f'{reader}"$CREDENTIAL"' not in source
        assert f"{reader}$CREDENTIAL" not in source
    # The only thing done with it is the readability test.
    assert '[ ! -r "$CREDENTIAL" ]' in source
