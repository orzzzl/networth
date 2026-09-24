"""Restartable holder driver, strict schema reader and pipe-only return transport."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from networth import link_recovery, mac_identity
from networth.commands import automatic_link as driver
from networth.commands.authorize_automatic_link import ACK
from networth.link_recovery import (
    AUTOMATIC_SCHEMA,
    FORBIDDEN_FIELDS,
    SCHEMA,
    CorruptRecord,
    MintResult,
    RecoveryRecord,
    SecondCopyUnverified,
)
from networth.mac_identity import REQUIRED_HOLDER
from networth.tokenstore import Secret

FLOW = "e" * 32
TOKEN = "synthetic-driver-token-sentinel"
URL = "https://synthetic.invalid/driver-url-sentinel"
NOW = datetime.now(UTC)
COMMIT = "a" * 40


def mint() -> MintResult:
    return MintResult(FLOW, Secret(TOKEN), NOW, NOW + timedelta(minutes=30), 1800, URL)


def test_literal_v2_record_remains_readable(tmp_path: Path) -> None:
    # Persisted bytes must survive a future reader; do not derive the fixture
    # from the current writer or its schema/protocol constants.
    path = tmp_path / (FLOW + ".json")
    path.write_text(
        """{
            "schema": "networth.link-recovery.2",
            "protocol": "networth.automatic-link.1",
            "flow_id": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            "link_token": "synthetic-driver-token-sentinel",
            "hosted_url": "https://synthetic.invalid/driver-url-sentinel",
            "minted_at": "2026-09-24T12:00:00+00:00",
            "link_token_expires_at": "2026-09-24T12:30:00+00:00",
            "url_lifetime_seconds": 1800,
            "reap_after": "2026-09-24T19:00:00+00:00",
            "second_copy_verified_at": "2026-09-24T12:00:00+00:00",
            "second_copy_holder": "zelengs-macbook-air-2"
        }""",
        encoding="utf-8",
    )
    record = link_recovery.load(tmp_path, FLOW)
    assert record.flow_id == FLOW
    assert record.link_token.reveal() == TOKEN
    assert record.hosted_url is not None and record.hosted_url.reveal() == URL
    assert record.protocol == "networth.automatic-link.1"
    assert json.loads(record.to_json())["schema"] == "networth.link-recovery.2"


@pytest.mark.parametrize("automatic", [False, True])
def test_exact_schema_literals_round_trip_and_keep_requested_lifetime(automatic: bool) -> None:
    record = mint().as_record(now=NOW, automatic=automatic)
    payload = json.loads(record.to_json())
    assert payload["schema"] == (AUTOMATIC_SCHEMA if automatic else SCHEMA)
    assert payload["url_lifetime_seconds"] == 1800
    restored = RecoveryRecord.from_json(record.to_json())
    assert restored.to_json() == record.to_json()
    assert TOKEN not in repr(restored) and URL not in repr(restored)


@pytest.mark.parametrize(
    "schema",
    [
        "networth.link-recovery.99",
        "networth.link-recovery.1suffix",
        "prefixnetworth.link-recovery.2",
        "synthetic-arbitrary-secret-schema",
        2,
        None,
        [],
        {},
    ],
)
@pytest.mark.parametrize("automatic", [False, True])
def test_unknown_and_malformed_schema_is_fixed_redacted_refusal(
    schema: Any, automatic: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = json.loads(mint().as_record(now=NOW, automatic=automatic).to_json())
    payload["schema"] = schema
    with pytest.raises(CorruptRecord) as error:
        RecoveryRecord.from_json(json.dumps(payload))
    assert str(error.value) == "unsupported recovery record schema"
    print(error.value)
    output = capsys.readouterr()
    assert TOKEN not in output.out and URL not in output.out
    assert "synthetic-arbitrary-secret-schema" not in output.out + output.err


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("field", sorted(FORBIDDEN_FIELDS))
def test_each_schema_refuses_every_guessed_deadline(automatic: bool, field: str) -> None:
    payload = json.loads(mint().as_record(now=NOW, automatic=automatic).to_json())
    payload[field] = "synthetic-arbitrary-value"
    with pytest.raises(CorruptRecord):
        RecoveryRecord.from_json(json.dumps(payload))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NETWORTH_ENV", "sandbox")
    monkeypatch.setenv("NETWORTH_LINK_RECOVERY_DIR", str(tmp_path / "recovery"))
    monkeypatch.setattr(mac_identity, "verify", lambda: REQUIRED_HOLDER)
    monkeypatch.setattr(driver, "_source", lambda _: "synthetic public source")
    return tmp_path / "recovery"


def args(resume: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(commit=COMMIT, resume=resume)


def test_restart_after_committed_release_lost_ack_mints_once(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # Actual VPS DB and store, separate from the holder's recovery directory.
    import sqlite3

    from networth.link_lifecycle import authorize_release
    from networth.link_recovery import AUTOMATIC_PROTOCOL
    from networth.storage import migrate
    from networth.tokenstore import SecretKind, TokenStore

    db = sqlite3.connect(tmp_path / "remote.sqlite")
    migrate(db)
    store = TokenStore(tmp_path / "remote-tokens")
    ref = store.put(SecretKind.LINK_TOKEN, FLOW, TOKEN)
    db.execute(
        "INSERT INTO link_request(flow_id, secret_ref, minted_at, hosted_url_expires_at, "
        "state, lifecycle_protocol) VALUES (?, ?, ?, ?, 'URL_MINTED', ?)",
        (FLOW, ref, NOW.isoformat(), (NOW + timedelta(minutes=30)).isoformat(), AUTOMATIC_PROTOCOL),
    )
    db.commit()
    calls: list[str] = []

    def remote(source: str, commit: str, verb: str, material: str = "") -> list[str]:
        calls.append(verb)
        if verb == "mint-automatic-link":
            return [mint().to_wire()]
        record = RecoveryRecord.from_json(material)
        assert link_recovery.load(env, FLOW).to_json() == material
        authorize_release(db, store, record, clock=lambda: NOW)
        if calls.count("authorize-automatic-link") == 1:
            raise OSError("synthetic lost acknowledgement")
        return [ACK + FLOW]

    monkeypatch.setattr(driver, "_remote", remote)
    assert driver.run(args()) == 2
    assert (
        db.execute("SELECT url_release_authorized_at FROM link_request").fetchone()[0] is not None
    )
    assert URL not in capsys.readouterr().out
    before = (env / (FLOW + ".json")).read_bytes()
    # Fresh invocation has no MintResult or previous call state to recover from.
    assert driver.run(args(FLOW)) == 0
    assert capsys.readouterr().out.strip() == URL
    assert (env / (FLOW + ".json")).read_bytes() == before
    assert calls == ["mint-automatic-link", "authorize-automatic-link", "authorize-automatic-link"]
    db.close()


def test_crash_before_return_leaves_verified_resume_record(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Crash(BaseException):
        pass

    def remote(source: str, commit: str, verb: str, material: str = "") -> list[str]:
        if verb == "mint-automatic-link":
            return [mint().to_wire()]
        raise Crash

    monkeypatch.setattr(driver, "_remote", remote)
    with pytest.raises(Crash):
        driver.run(args())
    assert link_recovery.load(env, FLOW).hosted_url is not None
    assert URL not in capsys.readouterr().out
    monkeypatch.setattr(driver, "_remote", lambda *a: [ACK + FLOW])
    assert driver.run(args(FLOW)) == 0
    assert capsys.readouterr().out.strip() == URL


@pytest.mark.parametrize(
    "failure", ["readback", "existing", "wrong_holder", "bad_ack", "hygiene", "truncated", "v1"]
)
def test_failure_withholds_url_and_never_remints_resume(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    calls: list[str] = []

    def remote(source: str, commit: str, verb: str, material: str = "") -> list[str]:
        calls.append(verb)
        return [mint().to_wire()] if verb == "mint-automatic-link" else [ACK + "f" * 32]

    monkeypatch.setattr(driver, "_remote", remote)
    resume = None
    if failure == "wrong_holder":

        def wrong() -> str:
            raise mac_identity.WrongHost("synthetic wrong host")

        monkeypatch.setattr(mac_identity, "verify", wrong)
    elif failure == "readback":
        monkeypatch.setattr(Path, "read_text", lambda *a, **k: "synthetic conflicting readback")
    elif failure in {"existing", "hygiene", "truncated", "v1"}:
        record = mint().as_record(now=NOW, automatic=failure != "v1")
        if failure == "hygiene":
            record = replace(record, reap_after=NOW - timedelta(seconds=1))
        link_recovery.store_and_verify(env, record, holder=REQUIRED_HOLDER, now=NOW)
        if failure != "existing":
            resume = FLOW
        if failure == "truncated":
            (env / (FLOW + ".json")).write_text('{"schema":')
    assert driver.run(args(resume)) == 2
    output = capsys.readouterr()
    assert URL not in output.out + output.err and TOKEN not in output.out + output.err
    if resume:
        assert calls == []
    if failure in {"existing", "readback", "wrong_holder"}:
        assert "authorize-automatic-link" not in calls


def test_resume_reestablishes_file_and_directory_barriers(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link_recovery.store_and_verify(
        env, mint().as_record(now=NOW, automatic=True), holder=REQUIRED_HOLDER, now=NOW
    )
    observed: list[int] = []
    original = os.fsync

    def fsync(fd: int) -> None:
        observed.append(fd)
        original(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    resumed = link_recovery.verify_for_resume(env, FLOW, holder=REQUIRED_HOLDER, now=NOW)
    assert resumed.hosted_url is not None and len(observed) == 2
    assert (env.stat().st_mode & 0o777) == 0o700
    assert ((env / (FLOW + ".json")).stat().st_mode & 0o777) == 0o600


def test_foreign_holder_resume_refused_before_remote_invocation(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    link_recovery.store_and_verify(
        env,
        mint().as_record(now=NOW, automatic=True),
        holder="synthetic-foreign-holder",
        now=NOW,
    )
    calls: list[str] = []

    def remote(source: str, commit: str, verb: str, material: str = "") -> list[str]:
        calls.append(verb)
        return [ACK + FLOW]

    monkeypatch.setattr(driver, "_remote", remote)
    with pytest.raises(SecondCopyUnverified, match="cannot authorize resume"):
        link_recovery.verify_for_resume(env, FLOW, holder=REQUIRED_HOLDER, now=NOW)
    assert driver.run(args(FLOW)) == 2
    assert calls == []
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err and URL not in output.out + output.err


def test_recovery_reaper_deletes_combined_v2_record(env: Path) -> None:
    link_recovery.store_and_verify(
        env, mint().as_record(now=NOW, automatic=True), holder=REQUIRED_HOLDER, now=NOW
    )
    assert link_recovery.reap_expired(env, now=NOW + timedelta(hours=6)).kept == (FLOW,)
    assert link_recovery.reap_expired(env, now=NOW + timedelta(hours=7)).deleted == (FLOW,)
    assert list(env.iterdir()) == []


def test_remote_bearers_only_in_stdin_never_argv_or_forwarded_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = mint().as_record(now=NOW, automatic=True)
    seen: list[list[str]] = []

    def invoke(argv: list[str], **kwargs: Any) -> Any:
        seen.append(argv)
        assert kwargs["input"] == record.to_json()
        assert TOKEN not in repr(argv) and URL not in repr(argv)
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(argv, 0, ACK + FLOW, "")

    monkeypatch.setattr(subprocess, "run", invoke)
    assert driver._remote(
        "synthetic public runner", COMMIT, "authorize-automatic-link", record.to_json()
    ) == [ACK + FLOW]
    assert seen[0][0] == "ssh" and "BatchMode=yes" in seen[0]


@pytest.mark.parametrize(
    "verb",
    [
        "mint-automatic-link",
        "authorize-automatic-link",
        "automatic-link",
        "close-link-request",
        "abandon-link-request",
    ],
)
def test_new_commands_refuse_production_before_material(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], verb: str
) -> None:
    from networth.cli import main

    monkeypatch.setenv("NETWORTH_ENV", "production")
    argv = [verb]
    if verb == "automatic-link":
        argv += ["--commit", COMMIT]
    if verb == "close-link-request":
        argv += ["--flow", FLOW, "--audit-id", FLOW, "--confirm-reviewed"]
    if verb == "abandon-link-request":
        argv += ["--flow", FLOW]
    assert main(argv) == 2
    assert URL not in capsys.readouterr().out


def test_measurement_completion_does_not_retire_v2_record(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io
    import sys

    from networth.cli import main

    record = link_recovery.store_and_verify(
        env, mint().as_record(now=NOW, automatic=True), holder=REQUIRED_HOLDER, now=NOW
    )
    outcome = link_recovery.CompletionOutcome(FLOW, link_recovery.EXCHANGED)
    monkeypatch.setattr(sys, "stdin", io.StringIO(outcome.to_wire()))
    assert main(["retire-hosted-link", "--flow", FLOW]) == 0
    assert link_recovery.load(env, FLOW).to_json() == record.to_json()
    assert URL not in capsys.readouterr().out
