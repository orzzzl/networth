"""Scheduled composition with synthetic state and no live credentials or network."""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from networth import runtime
from networth.backup.archive import verify_archive
from networth.backup.config import BackupConfig
from networth.pairing import PairingStore, StagedPayloadKey, new_provision, payload_key_ref
from networth.plaid.environment import PlaidCredentials, PlaidEnvironment, paths_for
from networth.publisher import Publisher
from networth.tokenstore import TokenStore

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    paths = paths_for(
        PlaidEnvironment.PRODUCTION, secrets_dir=tmp_path / "secrets", data_dir=tmp_path / "data"
    )
    monkeypatch.setenv("NETWORTH_ENV", "production")
    monkeypatch.setattr(runtime, "paths_for", lambda _: paths)
    monkeypatch.setattr(
        runtime,
        "load_credentials",
        lambda _: PlaidCredentials("synthetic", "synthetic", PlaidEnvironment.PRODUCTION),
    )
    return paths


def args(job: str) -> argparse.Namespace:
    return argparse.Namespace(job=job, flow=None, country_code=None)


def test_unlinked_production_initializes_and_syncs_without_any_provider_call(
    host: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    class NoCalls:
        def __init__(self, *_: Any) -> None:
            pass

        def __getattr__(self, name: str) -> Any:
            pytest.fail(f"empty runtime called provider: {name}")

    monkeypatch.setattr(runtime, "PlaidClient", NoCalls)
    assert runtime.run(args("init")) == 0
    assert runtime.run(args("sync")) == 0
    with sqlite3.connect(host.database) as db:
        assert db.execute("SELECT count(*) FROM item").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM link_request").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM snapshot").fetchone() == (1,)
        assert db.execute("SELECT ok FROM sync_run").fetchone() == (1,)
    assert runtime.run(args("publish")) == 0
    assert runtime.run(args("link-flow")) == 1


def test_invalid_environment_cannot_initialize(
    host: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(_: Any) -> Any:
        raise ValueError("sensitive-credential-must-not-print")

    monkeypatch.setattr(runtime, "load_credentials", refuse)
    assert runtime.run(args("init")) == 1
    assert not host.database.exists()
    assert "sensitive-credential" not in capsys.readouterr().err


def test_publish_retries_independently_and_pairing_rotation_is_immediately_due(
    host: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert runtime.run(args("sync")) == 0
    db = runtime.open_runtime_database(host.database)
    key_file = tmp_path / "payload.key"
    lock = tmp_path / "publish.lock"
    at = datetime.now(UTC)
    try:
        assert runtime.publish(db, lock, key_file, at=at) == "waiting for phone pairing"
        first = new_provision("synthetic.tail123.ts.net")
        with StagedPayloadKey(key_file, first.payload_key) as staged:
            PairingStore(db).rotate(
                first,
                key_ref=payload_key_ref(first.pairing_id),
                at=at,
                before_commit=staged.install,
            )
            staged.committed()
        original = Publisher.publish

        def fail(*_: Any, **__: Any) -> Any:
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(Publisher, "publish", fail)
        with pytest.raises(RuntimeError):
            runtime.publish(db, lock, key_file, at=at)
        assert db.execute("SELECT count(*) FROM publication").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM snapshot").fetchone() == (1,)
        monkeypatch.setattr(Publisher, "publish", original)
        assert runtime.publish(db, lock, key_file, at=at) == "published"
        assert runtime.publish(db, lock, key_file, at=at + timedelta(seconds=10)) == "not due"
        second = new_provision("synthetic.tail123.ts.net")
        with StagedPayloadKey(key_file, second.payload_key) as staged:
            PairingStore(db).rotate(
                second,
                key_ref=payload_key_ref(second.pairing_id),
                at=at,
                before_commit=staged.install,
            )
            staged.committed()
        assert runtime.publish(db, lock, key_file, at=at + timedelta(seconds=20)) == "published"
        assert db.execute("SELECT count(*) FROM sync_run").fetchone() == (1,)
    finally:
        db.close()


def test_archive_success_clock_survives_restart_and_missing_file_rebuilds(
    host: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert runtime.run(args("init")) == 0
    key_file = tmp_path / "backup.key"
    key_file.write_text((b"x" * 32).hex() + "\n")
    key_file.chmod(0o600)
    config = BackupConfig(host.database, host.items, tmp_path / "archives", key_file)
    monkeypatch.setattr(runtime, "load_backup_config", lambda: config)
    assert runtime.archive(at=NOW) == "built"
    verify_archive(config.archive_dir / "current.nwb", b"x" * 32)
    assert runtime.archive(at=NOW + timedelta(seconds=30)) == "not due"
    (config.archive_dir / "current.nwb").unlink()
    assert runtime.archive(at=NOW + timedelta(seconds=60)) == "built"
    assert runtime.archive(at=NOW + timedelta(minutes=6)) == "built"


def test_flow_selection_excludes_reaped_requests(tmp_path: Path) -> None:
    from networth.link_lifecycle import mint_request
    from tests.test_link_lifecycle import NOW as LINK_NOW
    from tests.test_link_lifecycle import Client

    db = runtime.open_runtime_database(tmp_path / "db")
    tokens = TokenStore(tmp_path / "tokens")
    try:
        first = mint_request(db, tokens, Client(), country_codes=("US",), clock=lambda: LINK_NOW)
        assert runtime.pending_flows(db) == [first.flow_id]
        db.execute(
            "UPDATE link_request SET material_reaped_at = ?",
            (NOW.isoformat().replace("+00:00", "Z"),),
        )
        db.commit()
        assert runtime.pending_flows(db) == []
    finally:
        db.close()


@pytest.mark.parametrize("environment,expected", [("sandbox", 2), ("production", 2)])
def test_link_supervisor_does_not_queue_second_flow_behind_running_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: str, expected: int
) -> None:
    monkeypatch.setenv("NETWORTH_ENV", environment)
    monkeypatch.setattr(runtime, "pending_flows", lambda _: ["a" * 32, "b" * 32])
    spawned: list[list[str]] = []
    scans = 0

    class StopLoop(BaseException):
        pass

    class Child:
        def __init__(self, command: list[str]) -> None:
            spawned.append(command)

        def poll(self) -> None:
            return None  # first child stays alive through both scans

        def wait(self) -> int:
            return 0

    def advance(_: float) -> None:
        nonlocal scans
        scans += 1
        if scans == 2:
            raise StopLoop

    monkeypatch.setattr(subprocess, "Popen", Child)
    monkeypatch.setattr(time, "sleep", advance)
    with pytest.raises(StopLoop):
        runtime.link_loop(tmp_path / "db", ("US",))
    assert len(spawned) == expected
    if expected:
        assert spawned[0][spawned[0].index("--flow") + 1] == "a" * 32
        assert spawned[1][spawned[1].index("--flow") + 1] == "b" * 32
    assert scans == 2
