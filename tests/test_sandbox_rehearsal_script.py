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


def fixture_origin(
    tmp_path: Path,
    *,
    build: str = "setuptools==84.0.0 \\\n    --hash=sha256:" + "a" * 64 + "\n",
    runtime: str = "urllib3==2.7.0 \\\n    --hash=sha256:" + "b" * 64 + "\n",
    omit: str | None = None,
) -> tuple[Path, str]:
    """A local git repository standing in for the origin, and its commit id.

    The runner fetches its source over `git` precisely so the bytes are hash-checked
    rather than trusted, and that makes the whole path testable offline: point
    ``NETWORTH_REHEARSAL_ORIGIN`` at a directory and every guard downstream of the
    fetch runs for real, against a real fetch, with no network.
    """
    origin = tmp_path / "origin"
    (origin / "networth").mkdir(parents=True)
    (origin / "networth" / "__init__.py").write_text("")
    files = {"requirements-build.txt": build, "requirements-runtime.txt": runtime}
    for name, body in files.items():
        if name != omit:
            (origin / name).write_text(body)

    git = ["git", "-C", str(origin)]
    subprocess.run([*git[:1], "init", "--quiet", "-b", "main", str(origin)], check=True)
    for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
        subprocess.run([*git, "config", key, value], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "--quiet", "-m", "fixture"], check=True)
    sha = subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return origin, sha


def venv_stub(tmp_path: Path, *, verb_exit: int = 0, pip_log: Path | None = None) -> Path:
    """A ``python3`` whose ``-m venv`` produces a venv that behaves as told.

    ``pip`` records the arguments it was given, so the tests can assert what the
    installer was actually *asked* to do rather than scanning the script for a flag.
    """
    # Written line by line with single-quoted payloads: a `printf` template with the
    # log path folded into it produced `exit 0nn` and cost twenty minutes.
    log = f"""    echo 'echo "$*" >> {pip_log}'\n""" if pip_log is not None else ""
    return stub_path(
        tmp_path,
        "#!/bin/sh\n"
        'if [ "$1" != "-m" ]; then exit 1; fi\n'
        'mkdir -p "$3/bin"\n'
        "{\n"
        "    echo '#!/bin/sh'\n" + log + "    echo 'exit 0'\n"
        '} > "$3/bin/pip"\n'
        "{\n"
        "    echo '#!/bin/sh'\n"
        f"    echo 'exit {verb_exit}'\n"
        '} > "$3/bin/python"\n'
        'chmod 755 "$3/bin/pip" "$3/bin/python"\n',
    )


def run_against(
    tmp_path: Path, origin: Path, sha: str, *, verb_exit: int = 0, pip_log: Path | None = None
) -> subprocess.CompletedProcess[str]:
    workdir = tmp_path / "tmp"
    workdir.mkdir(exist_ok=True)
    stub = venv_stub(tmp_path, verb_exit=verb_exit, pip_log=pip_log)
    result = run(
        sha,
        "--paths-only",
        env={
            "PATH": f"{stub}:{os.environ['PATH']}",
            "NETWORTH_REHEARSAL_ORIGIN": str(origin),
        },
        tmpdir=workdir,
    )
    assert list(workdir.iterdir()) == [], "the workspace outlived the run that removes it"
    return result


def test_the_source_is_fetched_at_the_requested_commit_and_verified(tmp_path: Path) -> None:
    origin, sha = fixture_origin(tmp_path)

    result = run_against(tmp_path, origin, sha)

    assert result.returncode == 0, result.stderr
    assert f"source        verified at {sha}" in result.stdout
    assert f"origin        {origin}" in result.stdout, "a non-default origin must be in the record"


def test_a_commit_the_origin_does_not_have_stops_the_run(tmp_path: Path) -> None:
    """The reviewed commit has to be pushed; an unfetchable one is not it."""
    origin, _ = fixture_origin(tmp_path)

    result = run_against(tmp_path, origin, "0" * 40)

    assert result.returncode == 2
    assert "could not fetch" in result.stderr


def test_the_verb_exit_status_is_passed_through(tmp_path: Path) -> None:
    """A failed rehearsal reporting success is the shape of defect this project keeps
    finding; the runner is the last place it could be swallowed."""
    origin, sha = fixture_origin(tmp_path)

    assert run_against(tmp_path, origin, sha, verb_exit=9).returncode == 9


def test_a_requirement_without_a_hash_stops_the_run(tmp_path: Path) -> None:
    """The blocker this rewrite exists for: pinning the *commit* pins nothing about
    what executes beside it.

    A requirement with no hash is a requirement pip would be free to satisfy with
    whatever the index serves today, which is exactly what a reviewed commit is not.
    """
    origin, sha = fixture_origin(tmp_path, runtime="urllib3==2.7.0\n")

    result = run_against(tmp_path, origin, sha)

    assert result.returncode == 2
    assert "not pinned with a hash" in result.stderr
    assert "urllib3==2.7.0" in result.stderr


def test_one_requirement_carrying_two_hashes_does_not_cover_for_another_with_none(
    tmp_path: Path,
) -> None:
    """Counting hashes against requirements passes here; checking each one does not.

    This is the mutation that would survive the obvious implementation, so it is the
    one worth a test of its own.
    """
    origin, sha = fixture_origin(
        tmp_path,
        runtime=(
            "urllib3==2.7.0 \\\n"
            f"    --hash=sha256:{'b' * 64} \\\n"
            f"    --hash=sha256:{'c' * 64}\n"
            "six==1.17.0\n"
        ),
    )

    result = run_against(tmp_path, origin, sha)

    assert result.returncode == 2
    assert "six==1.17.0" in result.stderr


def test_a_requirement_that_is_not_version_pinned_stops_the_run(tmp_path: Path) -> None:
    origin, sha = fixture_origin(
        tmp_path, runtime=f"urllib3>=2.7.0 \\\n    --hash=sha256:{'b' * 64}\n"
    )

    result = run_against(tmp_path, origin, sha)

    assert result.returncode == 2
    assert "not ==-pinned" in result.stderr


def test_a_missing_requirements_file_stops_the_run(tmp_path: Path) -> None:
    origin, sha = fixture_origin(tmp_path, omit="requirements-build.txt")

    result = run_against(tmp_path, origin, sha)

    assert result.returncode == 2
    assert "requirements-build.txt is missing" in result.stderr


def test_an_empty_requirements_file_is_not_a_satisfied_lock(tmp_path: Path) -> None:
    """Every requirement in an empty file is pinned, vacuously. That must not pass."""
    origin, sha = fixture_origin(tmp_path, runtime="# nothing here\n")

    result = run_against(tmp_path, origin, sha)

    assert result.returncode == 2
    assert "lists no requirements at all" in result.stderr


def test_pip_is_never_allowed_to_resolve_anything(tmp_path: Path) -> None:
    """What the installer was *asked* to do, recorded from the installer itself.

    `--require-hashes` refuses an unpinned requirement, `--no-deps` stops pip walking
    to anything the lock does not name, and `--no-build-isolation` is what keeps the
    backend that builds `plaid-python` — which ships no wheel — inside the pinned set
    instead of being fetched at build time.
    """
    origin, sha = fixture_origin(tmp_path)
    log = tmp_path / "pip.log"

    assert run_against(tmp_path, origin, sha, pip_log=log).returncode == 0

    invocations = log.read_text().splitlines()
    assert len(invocations) == 2, invocations
    for line in invocations:
        assert "--require-hashes" in line
        assert "--no-deps" in line
    assert "--no-build-isolation" in invocations[1]
    assert "--no-build-isolation" not in invocations[0], "the backend itself must be isolated"


def test_this_package_is_never_built_on_the_host(tmp_path: Path) -> None:
    """`hatchling` is the one participant a lock of *dependencies* cannot pin.

    Running from the verified checkout over `PYTHONPATH` removes it from the host
    entirely — so `networth` itself is never handed to an installer at all. Asserted
    from what pip was asked to do, because the script's own comments discuss the old
    `git+` install and a text scan cannot tell a comment from an instruction.
    """
    origin, sha = fixture_origin(tmp_path)
    log = tmp_path / "pip.log"

    assert run_against(tmp_path, origin, sha, pip_log=log).returncode == 0

    for line in log.read_text().splitlines():
        # Every install is a requirements file and nothing else: no VCS URL, and no
        # bare package name that could name this project.
        assert "git+" not in line, line
        assert line.split()[-1].endswith(("requirements-build.txt", "requirements-runtime.txt"))
        assert line.split()[-2] == "-r", line
    assert 'PYTHONPATH="$src"' in SCRIPT.read_text()


def test_the_script_never_reads_the_credential_itself(tmp_path: Path) -> None:
    """It tests that the file is *readable* and then hands it to the program.

    The credential is read by `networth` inside the venv, from the path the
    owner installed (AGENTS.md rule 1). A script that opened it — to check a
    key, to pass a value, to "just check it" — would be a second reader on the host,
    and the one whose output is a transcript.
    """
    source = SCRIPT.read_text()

    assert "PLAID_SECRET" not in source
