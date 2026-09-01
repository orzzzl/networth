"""The hook has to block a real `git commit`, not merely exist.

These build a throwaway repository, point it at this repo's hook and scanner,
and drive git for real. A hook proven only by calling the script it wraps is a
hook that can be broken by a wrong `core.hooksPath` and still look installed.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import git, plaid_access_token, plaid_secret_split_across_lines


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


def test_hook_blocks_a_credential_assignment_split_across_lines(repo: Path) -> None:
    """The regression: a real `git commit`, no `--no-verify`, and it went through.

    `{"secret":\\n  "…"}` is valid JSON that any formatter may produce, and every
    match was line-oriented, so no single line ever held both the key and the
    value. This drives the hook rather than the scanner because the hook is what
    the defect was reported against.
    """
    payload = plaid_secret_split_across_lines()
    assert json.loads(payload), "the probe must be valid JSON, not a contrived string"
    assert not any(  # the point of the test: no line carries the whole shape
        line.lstrip().startswith('"secret"') and any(c.isdigit() for c in line)
        for line in payload.splitlines()
    )

    (repo / "cfg.json").write_text(payload + "\n", encoding="utf-8")
    git("add", "cfg.json", cwd=repo)

    result = git("commit", "-m", "add config", cwd=repo)

    assert result.returncode != 0, "the hook let a multiline credential assignment through"
    assert "SECRET-SHAPED" in result.stderr
    assert git("log", "--oneline", cwd=repo).returncode != 0  # no commit exists
