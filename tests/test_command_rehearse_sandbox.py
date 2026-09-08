"""``networth rehearse-sandbox`` — the wiring, which is two acceptance criteria.

Criterion 3: ``NETWORTH_ENV`` selects the credential file, the items file and the
database **together**, so a rehearsal physically cannot write Production history.
Criterion 4: a Sandbox credential in a file labelled production is a **startup
failure**. Both are properties of :mod:`networth.plaid.environment`; what is tested
here is that the verb cannot be made to skip them.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from networth.cli import discover
from networth.commands import rehearse_sandbox
from networth.plaid import environment as environment_module
from networth.plaid.client import PlaidClient
from networth.plaid.environment import PlaidCredentials
from networth.plaid.rehearsal import (
    ACCOUNT_FIELDS,
    HOLDING_FIELDS,
    SECURITY_FIELDS,
    SandboxRehearsal,
)
from tests.fake_plaid import ACCESS_TOKEN, INSTITUTION, ITEM_ID, FakeSandboxApi


def _args(**kwargs: Any) -> argparse.Namespace:
    namespace = argparse.Namespace(print_paths_only=False)
    for key, value in kwargs.items():
        setattr(namespace, key, value)
    return namespace


def _install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, env: str, declared: str | None = None
) -> Path:
    """Lay out a host: the two credential files, and a data directory."""
    secrets = tmp_path / "etc-networth"
    secrets.mkdir()
    for name, label in (("plaid-sandbox.env", "sandbox"), ("plaid.env", "production")):
        body = declared if declared is not None and label == env else label
        (secrets / name).write_text(
            f"PLAID_ENV={body}\nPLAID_CLIENT_ID=synthetic-id\nPLAID_SECRET=synthetic-secret\n"
        )
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(environment_module, "SECRETS_DIR", secrets)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("NETWORTH_ENV", env)
    return secrets


def test_no_networth_env_is_a_refusal_not_a_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Defaulting would pick an environment, and the wrong pick is the permanent one."""
    monkeypatch.delenv("NETWORTH_ENV", raising=False)

    assert rehearse_sandbox.run(_args()) == 2

    assert "NETWORTH_ENV is required" in capsys.readouterr().err


def test_production_is_refused_before_the_credential_file_is_even_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal has to land before `load_credentials`, not after."""
    _install(monkeypatch, tmp_path, env="production")
    opened: list[Path] = []
    monkeypatch.setattr(
        environment_module,
        "read_env_file",
        lambda path, describe: opened.append(path) or {},  # type: ignore[func-returns-value]
    )

    assert rehearse_sandbox.run(_args()) == 2

    assert "refusing to rehearse against 'production'" in capsys.readouterr().err
    assert opened == []


def test_the_three_paths_printed_are_the_ones_the_environment_selected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 3, in the transcript: the run says which database it could write."""
    _install(monkeypatch, tmp_path, env="sandbox")

    assert rehearse_sandbox.run(_args(print_paths_only=True)) == 0

    out = capsys.readouterr().out
    assert "environment   sandbox" in out
    assert "plaid-sandbox.env" in out
    assert "networth-sandbox.db" in out
    assert "networth.db\n" not in out.replace("networth-sandbox.db", "")


def test_a_credential_file_whose_plaid_env_disagrees_is_a_startup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 4. The mismatch stops the process; it never becomes a live call."""
    _install(monkeypatch, tmp_path, env="sandbox", declared="production")

    assert rehearse_sandbox.run(_args()) == 2

    err = capsys.readouterr().err
    assert "declares a PLAID_ENV that is not 'sandbox'" in err
    assert "synthetic-secret" not in err


def _rehearsal_over_a_fake_sdk(credentials: PlaidCredentials, **kwargs: Any) -> SandboxRehearsal:
    """The real verb, the real rehearsal, the real client — a synthetic SDK."""
    return SandboxRehearsal(
        credentials, client=PlaidClient(credentials, api=FakeSandboxApi()), **kwargs
    )


def test_a_run_reports_every_field_and_no_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, tmp_path, env="sandbox")
    monkeypatch.setattr(rehearse_sandbox, "SandboxRehearsal", _rehearsal_over_a_fake_sdk)

    assert rehearse_sandbox.run(_args()) == 0

    out = capsys.readouterr().out
    for path in ACCOUNT_FIELDS + HOLDING_FIELDS + SECURITY_FIELDS:
        assert path in out
    assert "accounts (1 records)" in out
    # No figure, no institution, no item id, no token — the report is presence
    # and type, and that is the whole discipline of it.
    assert "1000.0" not in out
    assert INSTITUTION not in out
    assert ITEM_ID not in out
    assert ACCESS_TOKEN not in out


def test_the_verb_is_discovered_by_the_cli() -> None:
    """Auto-discovery is the contract (task 02): a file is a verb, with no list."""
    assert "rehearse-sandbox" in discover()
