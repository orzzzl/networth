"""``networth complete-hosted-link`` — the retrieval half, and what it must not do.

The expensive mistakes here are not failures, they are *helpful* successes: a
``--retrieve-only`` run that exchanges anyway destroys measurement (i) before it
starts, and a ``--from-tty`` run that stores an ``access_token`` answers measurement
(iv) while widening the laptop §15 keeps credentials off. Both are asserted directly.
"""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from plaid.exceptions import ApiException

from networth.cli import discover
from networth.commands import complete_hosted_link
from networth.plaid import environment as environment_module
from networth.plaid.client import PlaidClient
from networth.plaid.environment import PlaidCredentials, paths_for, selected_environment
from networth.tokenstore import SecretKind, TokenStore
from tests.fake_plaid import (
    ACCESS_TOKEN,
    ITEM_ID,
    LINK_TOKEN,
    PUBLIC_TOKEN,
    FakeSandboxApi,
    completed_session,
    link_sessions_response,
)

FLOW_ID = "0123456789abcdef0123456789abcdef"
SECOND_PUBLIC_TOKEN = "public-sandbox-synthetic-2"


def _args(**kwargs: Any) -> argparse.Namespace:
    namespace = argparse.Namespace(
        flow=FLOW_ID,
        from_tty=False,
        retrieve_only=False,
        exchange=False,
        exchange_twice=False,
    )
    for key, value in kwargs.items():
        setattr(namespace, key, value)
    return namespace


def _install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, env: str) -> None:
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


def _store_the_link_token(environment: Any = None) -> TokenStore:
    store = TokenStore(paths_for(environment or selected_environment()).items)
    store.put(SecretKind.LINK_TOKEN, FLOW_ID, LINK_TOKEN)
    return store


def _over_a_fake_sdk(monkeypatch: pytest.MonkeyPatch, api: Any) -> None:
    def build(credentials: PlaidCredentials, *args: Any, **kwargs: Any) -> PlaidClient:
        return PlaidClient(credentials, api=api)

    monkeypatch.setattr(complete_hosted_link, "PlaidClient", build)


class _Finished(FakeSandboxApi):
    """A Plaid whose session is complete, carrying the tokens it is given."""

    def __init__(self, *, public_tokens: tuple[str, ...] = (PUBLIC_TOKEN,), **kw: Any) -> None:
        super().__init__(**kw)
        self._public_tokens = public_tokens

    def link_token_get(self, link_token_get_request: Any) -> Any:
        return self._answer(
            "link_token_get",
            link_sessions_response(sessions=[completed_session(public_tokens=self._public_tokens)]),
            link_token_get_request,
        )

    def item_get(self, item_get_request: Any) -> Any:
        # Overridden deliberately: the base fake refuses, so a test that reaches
        # /item/get has to say it meant to. Measurement (ii)'s second half is the
        # only caller, and it is the half that decides 07a's recovery.
        return self._answer(
            "item_get",
            SimpleNamespace(item=SimpleNamespace(item_id=ITEM_ID, error=None), request_id="req-ig"),
            item_get_request,
        )


def test_the_verb_is_discovered_without_a_registry_edit() -> None:
    assert "complete-hosted-link" in discover()


def test_production_is_refused_before_any_credential_is_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, tmp_path, env="production")

    def record(env: environment_module.PlaidEnvironment) -> PlaidCredentials:
        raise AssertionError("the Production secret must not be read into this process")

    monkeypatch.setattr(environment_module, "load_credentials", record)

    assert complete_hosted_link.run(_args(exchange=True)) == 2

    assert "refusing to run against 'production'" in capsys.readouterr().err


def test_retrieve_only_does_not_exchange(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Measurement (i) is destroyed by a helpful exchange, so this is asserted at the SDK.

    Not by reading the output: the question is whether the *call* happened, and the
    only witness that cannot be satisfied by a reassuring log line is the recorded
    list of SDK requests.
    """
    _install(monkeypatch, tmp_path, env="sandbox")
    _store_the_link_token()
    api = _Finished()
    _over_a_fake_sdk(monkeypatch, api)

    assert complete_hosted_link.run(_args(retrieve_only=True)) == 0

    assert "item_public_token_exchange" not in api.called
    out = capsys.readouterr().out
    assert "was NOT exchanged" in out
    assert "measurement (i)" in out


def test_exchange_takes_every_public_token_not_just_the_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each token is a slot already spent (F2a); dropping one abandons a paid-for Item."""
    _install(monkeypatch, tmp_path, env="sandbox")
    _store_the_link_token()
    api = _Finished(public_tokens=(PUBLIC_TOKEN, SECOND_PUBLIC_TOKEN))
    _over_a_fake_sdk(monkeypatch, api)

    assert complete_hosted_link.run(_args(exchange=True)) == 0

    assert api.called.count("item_public_token_exchange") == 2
    assert "exchanged     2 public_token(s)" in capsys.readouterr().out


def test_an_unfinished_session_is_a_measurement_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, tmp_path, env="sandbox")
    _store_the_link_token()
    api = FakeSandboxApi()  # the default is the pre-start shape: no link_sessions key
    _over_a_fake_sdk(monkeypatch, api)

    assert complete_hosted_link.run(_args(exchange=True)) == 1

    assert "item_public_token_exchange" not in api.called
    assert "nothing was exchanged and nothing was spent" in capsys.readouterr().out.lower()


def test_the_access_token_is_never_printed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, tmp_path, env="sandbox")
    _store_the_link_token()
    _over_a_fake_sdk(monkeypatch, _Finished())

    assert complete_hosted_link.run(_args(exchange=True)) == 0

    captured = capsys.readouterr()
    assert ACCESS_TOKEN not in captured.out
    assert ACCESS_TOKEN not in captured.err
    assert ITEM_ID not in captured.out, "item_id names one of the owner's institutions"


def test_from_tty_stores_nothing_at_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Measurement (iv) answers a question; it must not also widen this laptop (§15).

    A successful exchange on the Mac is the whole point of (iv) *and* the moment an
    ``access_token`` could land here. The token store must not exist afterwards.
    """
    _install(monkeypatch, tmp_path, env="sandbox")
    answers = iter(["tty-client-id", "tty-secret", "tty-link-token"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt: next(answers))

    def refuse_files(env: environment_module.PlaidEnvironment) -> PlaidCredentials:
        raise AssertionError("--from-tty must not read this host's credential files")

    monkeypatch.setattr(environment_module, "load_credentials", refuse_files)
    _over_a_fake_sdk(monkeypatch, _Finished())

    assert complete_hosted_link.run(_args(flow=None, from_tty=True, exchange=True)) == 0

    captured = capsys.readouterr()
    assert "persists no credential" in captured.out
    assert not paths_for(selected_environment()).items.exists(), (
        "a --from-tty run left an access_token on the machine §15 keeps them off"
    )
    for secret in ("tty-client-id", "tty-secret", "tty-link-token"):
        assert secret not in captured.out
        assert secret not in captured.err


def test_from_tty_and_flow_are_different_sources_and_refuse_to_mix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, tmp_path, env="sandbox")

    assert complete_hosted_link.run(_args(from_tty=True, exchange=True)) == 2

    assert "different credential sources" in capsys.readouterr().err


def test_exchange_twice_records_the_refusal_and_still_probes_the_first_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both halves of measurement (ii), and the second half is the one that matters.

    A duplicate exchange that Plaid refuses is the expected branch — and ``07a``'s
    recovery still turns on whether the **first** ``access_token`` survived it. A run
    that stopped at the refusal would record the easy half and skip the deciding one.
    """
    _install(monkeypatch, tmp_path, env="sandbox")
    _store_the_link_token()

    class _RefusesTheSecond(_Finished):
        def __init__(self) -> None:
            super().__init__()
            self._exchanges = 0

        def item_public_token_exchange(self, request: Any) -> Any:
            self._exchanges += 1
            if self._exchanges > 1:
                # A real Plaid refusal, so the wrapper's redaction runs: the body
                # carries an error code and a planted secret, and neither may reach
                # the transcript this measurement produces.
                exc = ApiException(status=400, reason="Bad Request")
                exc.body = '{"error_code":"INVALID_PUBLIC_TOKEN","secret":"never-print-me"}'
                raise exc
            return super().item_public_token_exchange(request)

    api = _RefusesTheSecond()
    _over_a_fake_sdk(monkeypatch, api)

    assert complete_hosted_link.run(_args(exchange_twice=True)) == 0

    out = capsys.readouterr().out
    assert "second      REFUSED" in out
    assert "item_get" in api.called, "the deciding half of (ii) was skipped"
    assert "first token state=" in out
    # The refusal is printed, so the wrapper's redaction is on the transcript path.
    assert "never-print-me" not in out
    assert "INVALID_PUBLIC_TOKEN" not in out


def test_exchange_twice_records_an_accepted_duplicate_as_an_observation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """If Plaid accepts it, that is the measurement — not a test failure.

    06a's acceptance says "record each as a measurement whatever the result", and a
    verb that treated the surprising branch as an error would make the one outcome
    that changes the design the one outcome it cannot report.
    """
    _install(monkeypatch, tmp_path, env="sandbox")
    _store_the_link_token()
    api = _Finished()
    _over_a_fake_sdk(monkeypatch, api)

    assert complete_hosted_link.run(_args(exchange_twice=True)) == 0

    out = capsys.readouterr().out
    assert "second      ACCEPTED" in out
    assert "DESIGN.md records this observation" in out.replace("\n", " ").replace("  ", " ")
    assert "item_get" in api.called
