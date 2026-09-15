"""``networth start-hosted-link`` — the verb that is allowed to hand out a URL.

:mod:`networth.commands.probe_hosted_link` refuses to print a hosted URL because it
drops its only handle on exit. This verb prints one, and the tests here are about the
price of that: the ``link_token`` must be durable **before** anything openable reaches
the screen. By **F2a** a mint costs nothing and a completed Link costs a lifetime Item
slot, so the expensive failure is not "the store broke" — it is "the store broke and
the owner was handed a URL anyway", after which Plaid can spend the slot and **F7**
leaves no way to retrieve the ``public_token``.

So the ordering is asserted directly, not inferred from the happy path: one test reads
everything printed *at the moment the store is called* and requires the URL not to be
in it. Move the print above the write and that test goes red on its own.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from networth.cli import discover
from networth.commands import probe_hosted_link, start_hosted_link
from networth.plaid import environment as environment_module
from networth.plaid.client import PlaidClient
from networth.plaid.environment import PlaidCredentials, paths_for, selected_environment
from networth.tokenstore import SecretKind, TokenStore, TokenStoreError, secret_ref_for
from tests.fake_plaid import HOSTED_LINK_URL, LINK_TOKEN, FakeSandboxApi


def _args(**kwargs: Any) -> argparse.Namespace:
    namespace = argparse.Namespace(print_paths_only=False)
    for key, value in kwargs.items():
        setattr(namespace, key, value)
    return namespace


def _install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, env: str) -> None:
    """Lay out a host: the two credential files, and a data directory."""
    secrets = tmp_path / "etc-networth"
    secrets.mkdir()
    for name, label in (("plaid-sandbox.env", "sandbox"), ("plaid.env", "production")):
        (secrets / name).write_text(
            f"PLAID_ENV={label}\nPLAID_CLIENT_ID=synthetic-id\nPLAID_SECRET=synthetic-secret\n"
        )
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(environment_module, "SECRETS_DIR", secrets)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("NETWORTH_ENV", env)


def _over_a_fake_sdk(monkeypatch: pytest.MonkeyPatch, api: Any) -> None:
    """The real verb and the real client, with a synthetic SDK underneath."""

    def build(credentials: PlaidCredentials, *args: Any, **kwargs: Any) -> PlaidClient:
        return PlaidClient(credentials, api=api)

    monkeypatch.setattr(start_hosted_link, "PlaidClient", build)


def _flow_id_from(out: str) -> str:
    for line in out.splitlines():
        if line.startswith("flow id"):
            return line.split()[-1]
    raise AssertionError(f"no flow id was printed:\n{out}")


def test_the_verb_is_discovered_without_a_registry_edit() -> None:
    assert "start-hosted-link" in discover()


def test_no_networth_env_is_a_refusal_not_a_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NETWORTH_ENV", raising=False)

    assert start_hosted_link.run(_args()) == 2

    assert "NETWORTH_ENV is required" in capsys.readouterr().err


def test_production_is_refused_before_the_credential_file_is_even_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, tmp_path, env="production")
    opened: list[Path] = []

    def record(env: environment_module.PlaidEnvironment) -> PlaidCredentials:
        opened.append(paths_for(env).credentials)
        raise AssertionError("the Production secret must not be read into this process")

    monkeypatch.setattr(environment_module, "load_credentials", record)

    assert start_hosted_link.run(_args()) == 2

    captured = capsys.readouterr()
    assert opened == [], "the Production secret must not be read into this process at all"
    assert "refusing to mint a Hosted Link URL against 'production'" in captured.err
    assert HOSTED_LINK_URL not in captured.out


def test_the_production_refusal_is_the_probes_wording_not_a_second_phrasing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two phrasings of one rule drift apart; this pins them together.

    Compared by *running* both verbs rather than by matching source text, so the
    thing asserted is what the owner would actually read on his terminal. If someone
    reworks either message alone this goes red, and they have to decide whether both
    should move.
    """
    _install(monkeypatch, tmp_path, env="production")

    assert probe_hosted_link.run(_args()) == 2
    from_probe = capsys.readouterr().err
    assert start_hosted_link.run(_args()) == 2
    from_start = capsys.readouterr().err

    assert from_start == from_probe != ""


def test_print_paths_only_mints_nothing_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, tmp_path, env="sandbox")

    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("--print-paths-only must not reach Plaid")

    monkeypatch.setattr(start_hosted_link, "PlaidClient", explode)

    assert start_hosted_link.run(_args(print_paths_only=True)) == 0

    captured = capsys.readouterr()
    assert "environment   sandbox" in captured.out
    assert HOSTED_LINK_URL not in captured.out
    assert not paths_for(selected_environment()).items.exists()


def test_the_url_is_printed_and_the_link_token_is_durable_under_its_flow_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, tmp_path, env="sandbox")
    _over_a_fake_sdk(monkeypatch, FakeSandboxApi())

    assert start_hosted_link.run(_args()) == 0

    out = capsys.readouterr().out
    assert HOSTED_LINK_URL in out
    flow_id = _flow_id_from(out)
    ref = secret_ref_for(SecretKind.LINK_TOKEN, flow_id)
    assert f"secret ref    {ref}" in out

    store = TokenStore(paths_for(selected_environment()).items)
    assert store.get(ref).reveal() == LINK_TOKEN, (
        "the handle printed to the owner must resolve to the token /link/token/get needs"
    )


def test_the_link_token_itself_is_never_printed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The URL is spendable and is printed on purpose; the token is not and is not.

    They travel together through this verb, which is exactly when one gets printed
    beside the other by accident.
    """
    _install(monkeypatch, tmp_path, env="sandbox")
    _over_a_fake_sdk(monkeypatch, FakeSandboxApi())

    assert start_hosted_link.run(_args()) == 0

    captured = capsys.readouterr()
    assert LINK_TOKEN not in captured.out
    assert LINK_TOKEN not in captured.err


def test_a_failed_store_prints_no_url_at_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one genuinely expensive outcome, refused.

    The mint has already succeeded here — Plaid has issued a real, openable URL — and
    the durable handle did not land. Printing it anyway would let the owner spend a
    lifetime slot that **F7** then leaves no way to retrieve a ``public_token`` for.
    Refusing costs nothing: by **F2a** nothing is spent until Link completes.
    """
    _install(monkeypatch, tmp_path, env="sandbox")
    _over_a_fake_sdk(monkeypatch, FakeSandboxApi())

    def refuse(*args: Any, **kwargs: Any) -> str:
        raise TokenStoreError("synthetic durability failure")

    monkeypatch.setattr(TokenStore, "put", refuse)

    assert start_hosted_link.run(_args()) == 2

    captured = capsys.readouterr()
    assert HOSTED_LINK_URL not in captured.out
    assert HOSTED_LINK_URL not in captured.err
    assert "start-hosted-link failed" in captured.err


def test_nothing_openable_has_been_printed_by_the_time_the_store_is_called(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ordering itself, measured rather than inferred.

    The test above proves the *outcome* on a failure. This one proves the *sequence*
    on the happy path, which is the property that survives someone deciding the print
    reads better higher up: it reads everything stdout holds at the instant
    ``TokenStore.put`` is entered and requires the URL not to be among it.
    """
    _install(monkeypatch, tmp_path, env="sandbox")
    _over_a_fake_sdk(monkeypatch, FakeSandboxApi())

    printed_before_the_write: list[str] = []
    real_put = TokenStore.put

    def observing_put(self: TokenStore, *args: Any, **kwargs: Any) -> str:
        printed_before_the_write.append(capsys.readouterr().out)
        return str(real_put(self, *args, **kwargs))

    monkeypatch.setattr(TokenStore, "put", observing_put)

    assert start_hosted_link.run(_args()) == 0

    assert printed_before_the_write, "the store was never called"
    assert HOSTED_LINK_URL not in printed_before_the_write[0]
    # And it really is printed afterwards, so the assertion above cannot pass by the
    # URL never being printed at all.
    assert HOSTED_LINK_URL in capsys.readouterr().out


def test_this_verb_writes_no_link_flow_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``07a`` owns that table; a second untested writer in front of it is the defect.

    Asserted by the absence of the database file: the verb resolves a database path
    (it prints the paths it selects) and must not create one.
    """
    _install(monkeypatch, tmp_path, env="sandbox")
    _over_a_fake_sdk(monkeypatch, FakeSandboxApi())

    assert start_hosted_link.run(_args()) == 0
    capsys.readouterr()

    assert not paths_for(selected_environment()).database.exists()
