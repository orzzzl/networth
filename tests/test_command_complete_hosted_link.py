"""``networth complete-hosted-link`` — the retrieval half, and what it must not do.

The expensive mistakes here are not failures, they are *helpful* successes: a
``--retrieve-only`` run that exchanges anyway destroys measurement (i) before it
starts, and a ``--from-tty`` run that stores an ``access_token`` answers measurement
(iv) while widening the laptop §15 keeps credentials off. Both are asserted directly.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from plaid.exceptions import ApiException

from networth import link_recovery, mac_identity
from networth.cli import discover
from networth.commands import complete_hosted_link
from networth.link_recovery import RecoveryRecord
from networth.plaid import environment as environment_module
from networth.plaid.client import PlaidClient
from networth.plaid.environment import PlaidCredentials, paths_for, selected_environment
from networth.tokenstore import Secret, SecretKind, TokenStore
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
    # Pinned unconditionally, not only in the tests that use it. The default is
    # `~/agents/secrets/networth-link-recovery` on the real machine, and a test that
    # forgot this line reached it -- the suite would have been reading, and on another
    # branch writing, the directory that holds live recovery records.
    monkeypatch.setenv(link_recovery.RECOVERY_DIRECTORY_ENV, str(tmp_path / "link-recovery"))


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


def _write_the_recovery_record(*, flow_id: str = FLOW_ID) -> None:
    """What `link-start.sh` leaves on this Mac: the second copy, already verified."""
    now = datetime(2026, 9, 15, 11, 0, tzinfo=UTC)
    link_recovery.store_and_verify(
        link_recovery.mac_recovery_directory(),
        RecoveryRecord(
            flow_id=flow_id,
            link_token=Secret(LINK_TOKEN),
            minted_at=now,
            link_token_expires_at=None,
            url_lifetime_seconds=None,
            reap_after=link_recovery.reap_after_from(now),
        ),
        holder="zelengs-macbook-air-2",
        now=now,
    )


def _answer_the_terminal(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    """Stand in for the terminal read, never for the decision to require one.

    The prompt's *refusal* is what matters and cannot be observed through a patch of
    itself, so it is pinned in a subprocess below instead."""
    remaining = iter(answers)
    monkeypatch.setattr(complete_hosted_link, "_read_from_tty", lambda prompt: next(remaining))


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
    _write_the_recovery_record()
    _answer_the_terminal(monkeypatch, ["tty-client-id", "tty-secret"])

    def refuse_files(env: environment_module.PlaidEnvironment) -> PlaidCredentials:
        raise AssertionError("--from-tty must not read this host's credential files")

    monkeypatch.setattr(environment_module, "load_credentials", refuse_files)
    _over_a_fake_sdk(monkeypatch, _Finished())

    assert complete_hosted_link.run(_args(from_tty=True, exchange=True)) == 0

    captured = capsys.readouterr()
    assert "persists no credential" in captured.out
    assert not paths_for(selected_environment()).items.exists(), (
        "a --from-tty run left an access_token on the machine §15 keeps them off"
    )
    for secret in ("tty-client-id", "tty-secret", LINK_TOKEN):
        assert secret not in captured.out
        assert secret not in captured.err


def test_from_tty_without_a_flow_cannot_name_a_recovery_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two used to be mutually exclusive, because --from-tty prompted for the
    token. It reads the record now, so the flow id is what says *which* record --
    and a refusal is the only honest answer when nothing names one."""
    _install(monkeypatch, tmp_path, env="sandbox")

    assert complete_hosted_link.run(_args(flow=None, from_tty=True, exchange=True)) == 2

    assert "--flow <id> is required" in capsys.readouterr().err


def test_from_tty_refuses_before_prompting_when_no_record_names_the_flow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ordering, and it is the owner's time being protected rather than a secret.

    A missing record is the likeliest failure on this path. Discovering it *after* he
    has fetched his Plaid secret from the dashboard and typed it in would have spent
    the one manual step this command has, for nothing."""
    _install(monkeypatch, tmp_path, env="sandbox")

    def refuse(prompt: str) -> str:
        raise AssertionError("the record must be read before the owner is prompted")

    monkeypatch.setattr(complete_hosted_link, "_read_from_tty", refuse)

    assert complete_hosted_link.run(_args(from_tty=True, exchange=True)) == 2

    assert "no recovery record" in capsys.readouterr().err


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
    # 06a requires the duplicate exchange's error *code* to be recorded — it is what
    # 07a's EXCHANGE_UNCERTAIN recovery branches on — so the exact code must survive.
    assert "error_code  'INVALID_PUBLIC_TOKEN'" in out
    # And the rest of the body must not, from the same run: the code is lifted by a
    # grammar, not by widening the redaction. Both assertions have to hold together
    # or the measurement is being bought with the promise.
    assert "never-print-me" not in out
    assert "Bad Request" not in out


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


def test_piped_input_is_refused_because_there_is_no_controlling_terminal(
    tmp_path: Path,
) -> None:
    """The one assertion that cannot be made in-process, so it is made in a child.

    Every other ``--from-tty`` test stands in for the terminal read, which means none
    of them can observe the thing task 06a actually requires: that input arriving on
    **stdin** is refused rather than accepted. The previous implementation called
    ``getpass.getpass``, and fed three synthetic lines with no terminal available it
    accepted all three and returned normally, warning ``GetPassWarning: Can not
    control echo on the terminal``. A warning is not a refusal, and monkeypatched
    tests could not see the difference.

    ``start_new_session=True`` is ``setsid()``: the child gets its own session and
    therefore **no controlling terminal**, whoever ran the suite and whether or not
    they had one. So the discriminator is the same on this laptop and in CI.

    The network is not merely asserted away, it is removed: ``sitecustomize`` in the
    child's path makes ``socket.connect`` raise, so "before any Plaid call" is
    enforced by the environment rather than by reading the transcript afterwards.
    """
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        "import socket\n"
        "def _refuse(*a, **k):\n"
        "    raise RuntimeError('the suite makes no live call (task 05)')\n"
        "socket.socket.connect = _refuse\n"
        "socket.create_connection = _refuse\n",
        encoding="utf-8",
    )
    recovery = tmp_path / "link-recovery"
    now = datetime(2026, 9, 15, 11, 0, tzinfo=UTC)
    link_recovery.store_and_verify(
        recovery,
        RecoveryRecord(
            flow_id=FLOW_ID,
            link_token=Secret(LINK_TOKEN),
            minted_at=now,
            link_token_expires_at=None,
            url_lifetime_seconds=None,
            reap_after=link_recovery.reap_after_from(now),
        ),
        holder="zelengs-macbook-air-2",
        now=now,
    )

    environ = dict(os.environ)
    environ["NETWORTH_ENV"] = "sandbox"
    environ[link_recovery.RECOVERY_DIRECTORY_ENV] = str(recovery)
    environ["PYTHONPATH"] = str(shim)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "networth",
            "complete-hosted-link",
            "--from-tty",
            "--flow",
            FLOW_ID,
            "--exchange",
        ],
        input="piped-client-id\npiped-secret-never-read\n",
        capture_output=True,
        text=True,
        env=environ,
        start_new_session=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 2, completed.stderr
    assert "needs a controlling terminal" in completed.stderr
    transcript = completed.stdout + completed.stderr
    assert "piped-secret-never-read" not in transcript
    assert "GetPassWarning" not in transcript
    assert "the suite makes no live call" not in transcript, "a Plaid call was attempted"


# --- the normal end of a record's life ----------------------------------------


def _mac_record(now: datetime) -> Path:
    directory = link_recovery.mac_recovery_directory()
    link_recovery.store_and_verify(
        directory,
        link_recovery.RecoveryRecord(
            flow_id=FLOW_ID,
            link_token=Secret(LINK_TOKEN),
            minted_at=now,
            link_token_expires_at=None,
            url_lifetime_seconds=None,
            reap_after=link_recovery.reap_after_from(now),
        ),
        holder="test-host",
        now=now,
    )
    return link_recovery.record_path(directory, FLOW_ID)


def _on_the_mac(monkeypatch: pytest.MonkeyPatch, *, yes: bool) -> None:
    """Decide which machine this verb believes it is running on.

    The measurement is a real bind against the pinned tailnet address, so without
    this the answer would be "whichever computer ran the suite" — green on
    `zelengs-macbook-air-2` and red in CI, or the reverse, for tests whose subject
    is precisely the difference between the two hosts. `verify` resolves
    `holds_address` at call time, so replacing the module attribute reaches it.
    """
    monkeypatch.setattr(mac_identity, "holds_address", lambda _address: yes)


def test_the_mac_side_completion_retires_the_record_in_this_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`scripts/link-recover.sh`'s path: `--from-tty`, on the Mac, so the process
    that knows the exchange happened is also the one holding the record (§4).

    The record exists so a *stranded* flow can still be recovered; an exchanged
    flow cannot be stranded, so keeping its link token is residue."""
    _install(monkeypatch, tmp_path, env="sandbox")
    _on_the_mac(monkeypatch, yes=True)
    _store_the_link_token()
    record = _mac_record(datetime(2026, 9, 15, 11, 0, tzinfo=UTC))
    _over_a_fake_sdk(monkeypatch, _Finished())

    assert complete_hosted_link.run(_args(exchange=True)) == 0

    assert not record.exists()
    assert "cleaned" in capsys.readouterr().out


def test_the_vps_half_reports_the_exchange_and_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """PR #75 review blocker 2, and the reason the previous test was not enough.

    The normal path runs this verb over ssh (`sandbox-rehearsal-remote.sh`), so it
    executes **on the VPS** while the record is a file on the Mac. The old test put
    both in one process and one directory, which made a VPS-local deletion
    indistinguishable from the Mac's — so the suite reported a cleanup that the
    real topology could never perform, and every completed Sandbox flow in fact
    left its record on the laptop until the seven-hour sweep.

    The two hosts are separated here the way they are separated in life: the VPS
    process resolves its own recovery directory, which is a different directory,
    and the Mac's record is untouchable from it. The verb's obligation is to
    *report*, and the marker it prints is what lets the Mac finish the job.
    """
    _install(monkeypatch, tmp_path, env="sandbox")
    _store_the_link_token()

    # Written while the process still believes it is the Mac, then the host
    # changes underneath it -- which is the one way to get two genuinely
    # different `mac_recovery_directory()` answers inside a single test.
    _on_the_mac(monkeypatch, yes=True)
    mac_record = _mac_record(datetime(2026, 9, 15, 11, 0, tzinfo=UTC))
    vps_directory = tmp_path / "vps-link-recovery"
    monkeypatch.setenv(link_recovery.RECOVERY_DIRECTORY_ENV, str(vps_directory))
    _on_the_mac(monkeypatch, yes=False)
    _over_a_fake_sdk(monkeypatch, _Finished())

    assert complete_hosted_link.run(_args(exchange=True)) == 0

    captured = capsys.readouterr()
    assert mac_record.exists(), "the VPS cannot delete a file on the Mac, and must not seem to"
    assert not vps_directory.exists(), "the VPS must not so much as resolve the Mac's directory"
    assert "not this machine's to remove" in captured.out
    assert "cleaned" not in captured.out

    # The one line that authorises the Mac to delete: a positive report, naming
    # this flow. An exit status could not have said it -- `--retrieve-only` exits
    # zero too, and it is the mode whose record must survive.
    markers = [
        line
        for line in captured.out.splitlines()
        if line.startswith(link_recovery.COMPLETION_WIRE_MARKER)
    ]
    assert len(markers) == 1
    outcome = link_recovery.CompletionOutcome.from_wire(markers[0])
    assert outcome == link_recovery.CompletionOutcome(FLOW_ID, link_recovery.EXCHANGED)


def test_retrieve_only_keeps_the_record_because_that_flow_is_still_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Measurement (i) comes back to this same flow after 30 minutes and needs the
    record to do it. Cleaning up here would delete the input to the measurement.

    Checked on the Mac — the host that *can* delete — because "kept" is only
    evidence when deletion was possible. And no marker is emitted: on the remote
    path that line is the deletion authority, so printing one here would hand the
    Mac permission to destroy the record this mode exists to preserve."""
    _install(monkeypatch, tmp_path, env="sandbox")
    _on_the_mac(monkeypatch, yes=True)
    _store_the_link_token()
    record = _mac_record(datetime(2026, 9, 15, 11, 0, tzinfo=UTC))
    _over_a_fake_sdk(monkeypatch, _Finished())

    assert complete_hosted_link.run(_args(retrieve_only=True)) == 0

    captured = capsys.readouterr()
    assert record.exists()
    assert link_recovery.COMPLETION_WIRE_MARKER not in captured.out


def test_a_cleanup_fault_is_a_note_and_never_fails_a_landed_exchange(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-zero exit here would tell the owner the exchange did not land, send him
    to a recovery procedure for a finished flow, and be false."""
    _install(monkeypatch, tmp_path, env="sandbox")
    _on_the_mac(monkeypatch, yes=True)
    _store_the_link_token()
    _mac_record(datetime(2026, 9, 15, 11, 0, tzinfo=UTC))
    _over_a_fake_sdk(monkeypatch, _Finished())

    def refuse(*args: Any, **kwargs: Any) -> bool:
        raise OSError("synthetic unlink failure")

    monkeypatch.setattr(link_recovery, "delete", refuse)

    assert complete_hosted_link.run(_args(exchange=True)) == 0

    captured = capsys.readouterr()
    assert "exchanged     1 public_token(s)" in captured.out
    assert "was not removed" in captured.err
