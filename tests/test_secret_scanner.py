"""The scanner must fire on each credential shape, and stay quiet otherwise.

One test per shape, each asserting a non-zero exit (task 02). The shapes are
assembled at run time by `conftest.py`; nothing credential-shaped is committed.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.conftest import (
    REPO_ROOT,
    Planted,
    git,
    plaid_access_token,
    plaid_client_id_and_secret,
    plaid_secret_split_across_lines,
    private_key_header,
    run_scanner,
)


@pytest.mark.parametrize(
    ("shape", "make"),
    [
        ("plaid access token", plaid_access_token),
        ("client_id/secret pair", plaid_client_id_and_secret),
        ("secret assignment split across lines", plaid_secret_split_across_lines),
        ("private key header", private_key_header),
    ],
)
def test_scanner_fails_on_planted_secret(
    shape: str, make: Callable[[], str], planted: Planted
) -> None:
    result = run_scanner(planted(make()))
    assert result.returncode != 0, f"scanner passed a planted {shape}:\n{result.stdout}"
    assert "SECRET-SHAPED" in result.stderr


def test_scanner_reports_the_location_but_never_the_value(planted: Planted) -> None:
    """CI logs are a public artifact; a scanner that echoes the hit leaks it."""
    token = plaid_access_token()
    result = run_scanner(planted(token))
    assert token not in result.stderr
    assert token not in result.stdout
    assert "planted.txt:1" in result.stderr


def test_scanner_passes_on_ordinary_text(planted: Planted) -> None:
    ordinary = "\n".join(
        (
            "The item's access_token is stored in TokenStore, never here.",
            "PLAID_CLIENT_ID is read from /etc/networth/plaid.env at run time.",
            "See DESIGN.md section 15 for where the client_id and secret live.",
        )
    )
    result = run_scanner(planted(ordinary))
    assert result.returncode == 0, result.stderr


def test_scanner_reads_prose_about_credentials_without_firing() -> None:
    """The real repo is full of the *words*; firing on those gets it disabled."""
    result = run_scanner(cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr


def test_scanner_scans_every_tracked_file_and_says_how_many() -> None:
    """A clean result is only meaningful next to the count it covered.

    "0 file(s) scanned, clean" is the failure this asserts against: a scanner
    whose file list silently came back empty passes every time.
    """
    tracked = subprocess.run(  # noqa: S603
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()

    result = run_scanner(cwd=REPO_ROOT)

    assert result.returncode == 0, result.stderr
    assert len(tracked) > 0
    assert f"{len(tracked)} file(s) scanned, clean" in result.stdout


def test_scanner_refuses_to_pass_outside_a_repo_with_no_files_named(tmp_path: Path) -> None:
    """Exit 2, not 0. Returning "clean" because it found nothing to look at is
    the way a check goes green forever without anyone noticing."""
    result = run_scanner(cwd=tmp_path)
    assert result.returncode == 2
    assert "not a git repository" in result.stderr


def test_tracked_mode_reads_the_blob_so_a_symlink_cannot_hide_a_token(repo: Path) -> None:
    """Tracked mode is the CI backstop for a skipped or broken local hook.

    A symlink's blob content is its target text, and git publishes it. But
    `[[ -f path ]]` is false for a symlink whose target does not exist, so a
    scanner reading the working tree skipped the one entry it most needed to
    read — and reported every tracked path clean while the token was in the
    tree.
    """
    token = plaid_access_token()
    (repo / "evil_link").symlink_to(token)
    (repo / "ok.txt").write_text("nothing here\n", encoding="utf-8")
    git("add", "evil_link", "ok.txt", cwd=repo)

    # The hook still catches it on the way in...
    assert run_scanner("--staged", cwd=repo).returncode != 0

    # ...so model the case tracked mode exists for: the hook was bypassed.
    committed = git("commit", "--no-verify", "-m", "bypass", cwd=repo)
    assert committed.returncode == 0, committed.stderr

    result = run_scanner(cwd=repo)

    assert result.returncode != 0, f"tracked mode skipped the symlink blob:\n{result.stdout}"
    assert "SECRET-SHAPED" in result.stderr
    assert "evil_link" in result.stderr
    assert token not in result.stderr


def test_tracked_mode_still_sees_an_unstaged_edit(repo: Path) -> None:
    """Reading blobs must not make a local run blind to the file on disk.

    `scripts/check.sh` is what a developer runs before pushing; if switching to
    index blobs stopped it seeing uncommitted work, the fix for the symlink hole
    would have opened a plainer one.
    """
    (repo / "notes.md").write_text("nothing here\n", encoding="utf-8")
    git("add", "notes.md", cwd=repo)
    assert git("commit", "-m", "notes", cwd=repo).returncode == 0

    (repo / "notes.md").write_text(f'TOKEN = "{plaid_access_token()}"\n', encoding="utf-8")

    result = run_scanner(cwd=repo)

    assert result.returncode != 0, "an unstaged edit was invisible to tracked mode"
    assert "working copy" in result.stderr


def test_tracked_mode_skips_a_submodule_instead_of_failing(repo: Path) -> None:
    """A gitlink is the index entry that has no blob at all.

    Reading blobs by object id is what closed the symlink hole, and a submodule
    stores a commit id rather than content — so the same code path has to skip it
    on purpose instead of treating an unreadable object as a scanner failure.
    Unhandled, it turned every run in a repo containing a submodule into exit 2:
    CI red, and no way to commit one.

    The gitlink is built with `update-index` so the test needs neither a second
    repository nor `protocol.file.allow`.
    """
    (repo / "ok.txt").write_text("nothing here\n", encoding="utf-8")
    git("add", "ok.txt", cwd=repo)
    assert git("commit", "-m", "ok", cwd=repo).returncode == 0
    head = git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    added = git("update-index", "--add", "--cacheinfo", f"160000,{head},sub", cwd=repo)
    assert added.returncode == 0, added.stderr

    result = run_scanner(cwd=repo)

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "submodule" in result.stdout, "a skipped entry must be reported, not silently dropped"


def test_scanner_refuses_a_named_path_it_cannot_read(tmp_path: Path) -> None:
    """Exit 2, not 0 — same reason as the empty-file-list case above."""
    result = run_scanner(tmp_path / "absent.txt", cwd=tmp_path)
    assert result.returncode == 2
    assert "not a readable file" in result.stderr
