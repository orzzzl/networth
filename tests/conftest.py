"""Shared fixtures.

The credential-shaped strings the scanner tests need are **built at run time,
never committed**. A repository whose secret scanner is proven by a checked-in
file containing a Plaid-shaped token has put that shape in the permanent history
of a public repo in order to prove it would not (AGENTS.md rule 0). Assembling
them here from parts also keeps this file from matching the scanner's own
patterns, so the suite that tests the scanner does not trip it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER = REPO_ROOT / "scripts" / "check-no-secrets.sh"

_FAKE_UUID = "-".join(("0" * 8, "0" * 4, "0" * 4, "0" * 4, "0" * 12))

#: What the `planted` fixture returns: write text, get back the file holding it.
Planted = Callable[..., Path]


def plaid_access_token() -> str:
    """A string shaped exactly like a Plaid access token. Not a credential."""
    return "-".join(("access", "sandbox", "")) + _FAKE_UUID


def plaid_client_id_and_secret() -> str:
    """A `client_id`/`secret` pair, in the shape they appear in a config file."""
    return "\n".join(
        (
            "PLAID_" + "CLIENT_ID=" + "0" * 24,
            "PLAID_" + "SECRET=" + "0" * 30,
        )
    )


def private_key_header() -> str:
    """The first line of a PEM private key."""
    return "-" * 5 + "BEGIN OPENSSH PRIVATE KEY" + "-" * 5


def run_scanner(*args: str | Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(SCANNER), *map(str, args)],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


@pytest.fixture
def planted(tmp_path: Path) -> Callable[..., Path]:
    """Write `content` to a file under tmp_path and return its path."""

    def _planted(content: str, name: str = "planted.txt") -> Path:
        path = tmp_path / name
        path.write_text(content + "\n", encoding="utf-8")
        return path

    return _planted
