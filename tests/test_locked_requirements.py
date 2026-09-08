"""The exported requirement files are `uv.lock`, or they are a second source of truth.

`scripts/sandbox-rehearsal.sh` installs from `requirements-build.txt` and
`requirements-runtime.txt` rather than resolving anything, which is what makes "the
reviewed commit" a claim about what executes on the credential host instead of only
about which source was checked out. Two committed files derived from a third are a
drift waiting to happen, so the derivation is checked here: CI regenerates them from
the lock and fails on any difference.

`--frozen` matters as much as the comparison. Without it `uv export` would quietly
re-lock against today's index when `pyproject.toml` and `uv.lock` disagree, and the
test would compare a fresh resolution against itself and pass.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.conftest import REPO_ROOT

EXPORTS = {
    "requirements-runtime.txt": ("--no-dev",),
    "requirements-build.txt": ("--only-group", "build"),
}


def export(*flags: str) -> str:
    return subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            *flags,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is this project's toolchain")
@pytest.mark.parametrize(("name", "flags"), sorted(EXPORTS.items()))
def test_the_committed_export_still_matches_the_lock(name: str, flags: tuple[str, ...]) -> None:
    committed = (REPO_ROOT / name).read_text()

    assert committed == export(*flags), (
        f"{name} no longer matches uv.lock. Regenerate it:\n"
        f"  uv export --frozen --no-emit-project --format requirements-txt "
        f"{' '.join(flags)} -o {name}"
    )


@pytest.mark.parametrize("name", sorted(EXPORTS))
def test_every_exported_requirement_is_pinned_with_a_hash(name: str) -> None:
    """The property the host runner depends on, asserted where it is produced.

    The runner refuses a file that fails this, and that refusal is tested there. This
    is the other side: the file we actually ship must never be the one that trips it.
    """
    unhashed: list[str] = []
    current: str | None = None
    hashed = False
    for line in (REPO_ROOT / name).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            if current is not None and not hashed:
                unhashed.append(current)
            current, hashed = line.split()[0], False
            assert "==" in current, f"{current} in {name} is not pinned to one version"
        elif "--hash=sha256:" in line:
            hashed = True
    if current is not None and not hashed:
        unhashed.append(current)

    assert unhashed == [], f"{name} carries requirements with no hash: {unhashed}"


def test_the_runtime_export_does_not_carry_the_dev_toolchain() -> None:
    """pytest, ruff and mypy have no business on the host holding the Plaid secret."""
    runtime = (REPO_ROOT / "requirements-runtime.txt").read_text()

    for tool in ("pytest", "ruff==", "mypy"):
        assert tool not in runtime
