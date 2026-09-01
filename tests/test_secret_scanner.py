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
    plaid_access_token,
    plaid_client_id_and_secret,
    private_key_header,
    run_scanner,
)


@pytest.mark.parametrize(
    ("shape", "make"),
    [
        ("plaid access token", plaid_access_token),
        ("client_id/secret pair", plaid_client_id_and_secret),
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
