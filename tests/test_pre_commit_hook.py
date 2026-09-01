"""The hook has to block a real `git commit`, not merely exist.

These build a throwaway repository, point it at this repo's hook and scanner,
and drive git for real. A hook proven only by calling the script it wraps is a
hook that can be broken by a wrong `core.hooksPath` and still look installed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT, plaid_access_token


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *args], cwd=cwd, capture_output=True, text=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo wired to this project's hook and scanner."""
    work = tmp_path / "repo"
    (work / "scripts").mkdir(parents=True)
    (work / ".githooks").mkdir()

    for src in (
        REPO_ROOT / "scripts" / "check-no-secrets.sh",
        REPO_ROOT / ".githooks" / "pre-commit",
    ):
        dst = work / src.relative_to(REPO_ROOT)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        dst.chmod(0o755)

    git("init", "-q", "-b", "main", cwd=work)
    git("config", "user.email", "test@example.invalid", cwd=work)
    git("config", "user.name", "test", cwd=work)
    git("config", "core.hooksPath", ".githooks", cwd=work)
    return work


def test_hook_blocks_a_commit_carrying_a_secret(repo: Path) -> None:
    (repo / "config.py").write_text(f'TOKEN = "{plaid_access_token()}"\n', encoding="utf-8")
    git("add", "config.py", cwd=repo)

    result = git("commit", "-m", "add config", cwd=repo)

    assert result.returncode != 0, "the hook let a Plaid-shaped token through"
    assert "SECRET-SHAPED" in result.stderr
    assert git("log", "--oneline", cwd=repo).returncode != 0  # no commit exists


def test_hook_allows_an_ordinary_commit(repo: Path) -> None:
    (repo / "notes.md").write_text("Tokens live in /etc/networth/, never here.\n", encoding="utf-8")
    git("add", "notes.md", cwd=repo)

    result = git("commit", "-m", "add notes", cwd=repo)

    assert result.returncode == 0, result.stderr


def test_hook_reads_the_index_not_the_working_tree(repo: Path) -> None:
    """Stage a secret, then clean the working copy: the commit still carries it.

    This is the case a scanner that reads files off disk gets wrong, and it is
    not exotic — it is what `git add -p` plus an edit produces by accident.
    """
    target = repo / "config.py"
    target.write_text(f'TOKEN = "{plaid_access_token()}"\n', encoding="utf-8")
    git("add", "config.py", cwd=repo)
    target.write_text('TOKEN = os.environ["PLAID_ACCESS_TOKEN"]\n', encoding="utf-8")

    result = git("commit", "-m", "add config", cwd=repo)

    assert result.returncode != 0, "the hook scanned the working tree instead of the index"
    assert "SECRET-SHAPED" in result.stderr
