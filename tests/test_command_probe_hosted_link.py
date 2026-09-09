"""``networth probe-hosted-link`` — the half of F7 criterion 2 that needs no browser.

Completing a Hosted Link session has **no API**; Plaid requires a person in a browser
and advises against scripting the UI. So the verb measures the one thing that is
reachable without one — the shape of ``/link/token/get`` *before* completion — and the
tests here are mostly about what it must refuse to do while getting there, because the
artefact it produces (an openable hosted URL) is one completed Link away from a spent
lifetime Item slot (**F2a**).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from networth.cli import discover
from networth.commands import probe_hosted_link
from networth.plaid import environment as environment_module
from networth.plaid.client import PlaidClient
from networth.plaid.environment import PlaidCredentials, paths_for, selected_environment
from tests.fake_plaid import HOSTED_LINK_URL, LINK_TOKEN, FakeSandboxApi, link_sessions_response


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


def _over_a_fake_sdk(api: Any) -> Any:
    """The real verb and the real client, with a synthetic SDK underneath."""

    def build(credentials: PlaidCredentials) -> PlaidClient:
        return PlaidClient(credentials, api=api)

    return build


class _EmptySessionList(FakeSandboxApi):
    """A Plaid that sends ``link_sessions: []`` before completion instead of nothing.

    This is the shape the design would be wrong about, which is the whole reason
    criterion 2 is measured live rather than fixtured.
    """

    def link_token_get(self, link_token_get_request: Any) -> Any:
        return link_sessions_response(sessions=[])


def test_no_networth_env_is_a_refusal_not_a_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NETWORTH_ENV", raising=False)

    assert probe_hosted_link.run(_args()) == 2

    assert "NETWORTH_ENV is required" in capsys.readouterr().err


def test_production_is_refused_before_the_credential_file_is_even_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal lands before `load_credentials`, and it names what it is protecting.

    A Production hosted URL is not merely a call this verb should not make: it is a
    *spendable artefact* that would then be sitting in a transcript, one click from a
    lifetime Item slot. That is a different reason from `rehearse-sandbox`'s, and the
    message has to carry it or the next reader will assume they are the same refusal.
    """
    _install(monkeypatch, tmp_path, env="production")
    opened: list[Path] = []
    monkeypatch.setattr(
        environment_module,
        "read_env_file",
        lambda path, describe: opened.append(path) or {},  # type: ignore[func-returns-value]
    )

    assert probe_hosted_link.run(_args()) == 2

    err = capsys.readouterr().err
    assert "refusing to mint a Hosted Link URL against 'production'" in err
    assert "F2a" in err
    assert "task 08" in err
    assert opened == []


def test_print_paths_only_makes_no_plaid_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The runner's safe first run. Constructing a client at all would fail this."""
    _install(monkeypatch, tmp_path, env="sandbox")

    def explode(_credentials: PlaidCredentials) -> PlaidClient:
        raise AssertionError("--print-paths-only built a Plaid client")

    monkeypatch.setattr(probe_hosted_link, "PlaidClient", explode)

    assert probe_hosted_link.run(_args(print_paths_only=True)) == 0

    out = capsys.readouterr().out
    assert "environment   sandbox" in out
    assert "plaid-sandbox.env" in out


def test_the_absent_key_is_the_shape_that_makes_criterion_2a_hold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, tmp_path, env="sandbox")
    monkeypatch.setattr(probe_hosted_link, "PlaidClient", _over_a_fake_sdk(FakeSandboxApi()))

    assert probe_hosted_link.run(_args()) == 0

    out = capsys.readouterr().out
    assert "link_sessions SESSIONS_ABSENT" in out
    assert "criterion 2a  HOLDS" in out
    assert "public tokens 0" in out


def test_the_transcript_says_which_pre_completion_state_it_measured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """PR #59 review, finding 1. "Before completion" is more than one state.

    This run creates exactly one of them — nobody has opened the URL — and the
    started-but-unfinished state, which `07a` spends most of its ticks in, is
    unreachable without a browser. The transcript has to say so, because the next
    reader of it is deciding whether a branch is covered. An unqualified
    "criterion 2 HOLDS" is how a poller ends up deployed into a state nothing
    measured.
    """
    _install(monkeypatch, tmp_path, env="sandbox")
    monkeypatch.setattr(probe_hosted_link, "PlaidClient", _over_a_fake_sdk(FakeSandboxApi()))

    assert probe_hosted_link.run(_args()) == 0

    out = capsys.readouterr().out
    assert "state         PRE-START" in out
    assert "NOT measured here" in out
    assert "criterion 2   HOLDS" not in out, "the universal claim is not this run's to make"


def test_a_different_shape_is_reported_as_a_measurement_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 1, not 2 and not 0.

    "The API sent something the design did not assume" is a successful measurement
    with an unwelcome result. Exiting 0 would let a transcript record a criterion as
    proved when it was contradicted; exiting 2 would file it as a broken run and
    invite a retry instead of a design change.
    """
    _install(monkeypatch, tmp_path, env="sandbox")
    monkeypatch.setattr(probe_hosted_link, "PlaidClient", _over_a_fake_sdk(_EmptySessionList()))

    assert probe_hosted_link.run(_args()) == 1

    out = capsys.readouterr().out
    assert "link_sessions NO_SESSIONS" in out
    assert "criterion 2a  DOES NOT HOLD" in out
    assert "2a  HOLDS" not in out


def test_neither_the_hosted_url_nor_the_link_token_can_reach_the_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """PR #59 review, finding 2. There is no longer an option that widens this.

    The URL used to be printable with `--print-url`, for an owner-run half that does
    not exist yet — and this process holds the only handle to the session it mints and
    drops it on exit, so a URL it printed could be completed with nothing left to call
    `/link/token/get` with. That is an Item slot (**F2a**) spent for no measurement.
    Withholding it by default was not enough: the flag was the whole hazard, so it is
    gone rather than defaulted off.
    """
    _install(monkeypatch, tmp_path, env="sandbox")
    monkeypatch.setattr(probe_hosted_link, "PlaidClient", _over_a_fake_sdk(FakeSandboxApi()))

    assert probe_hosted_link.run(_args()) == 0

    out = capsys.readouterr().out
    assert HOSTED_LINK_URL not in out
    assert LINK_TOKEN not in out


def test_the_verb_offers_no_option_that_prints_the_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Asserted on the parser, not on the output.

    The test above would keep passing if the flag came back and simply defaulted to
    off; this one is what makes the removal a removal. A future half that needs the
    URL must add it back deliberately, with the surviving handle that makes it usable.
    """
    parser = argparse.ArgumentParser()
    probe_hosted_link.add_arguments(parser)

    flags = {flag for action in parser._actions for flag in action.option_strings}
    assert "--print-url" not in flags
    assert not [flag for flag in flags if "url" in flag]


def test_the_probe_persists_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No database, no token store, no `link_flow` row.

    A measurement that left a durable Link flow behind would be a half-built version
    of the driver §16 describes and task `07a` owns, and the next reader could not
    tell which one wrote the row.
    """
    _install(monkeypatch, tmp_path, env="sandbox")
    monkeypatch.setattr(probe_hosted_link, "PlaidClient", _over_a_fake_sdk(FakeSandboxApi()))
    paths = paths_for(selected_environment())

    assert probe_hosted_link.run(_args()) == 0

    capsys.readouterr()
    assert not paths.database.exists()
    assert not paths.items.exists()


def test_the_verb_is_discovered_by_the_cli() -> None:
    assert "probe-hosted-link" in discover()
