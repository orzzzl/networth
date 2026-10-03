"""Read commands through the real parser, SQLite WAL and synthetic files only."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from networth.cli import main
from networth.commands._read import database, emit
from networth.diagnostics import read_doctor
from networth.link_recovery import RecoveryRecord, store_and_verify
from networth.model import FreshnessPolicy
from networth.plaid import environment
from networth.query import NetWorthQuery
from networth.snapshotter import Snapshotter
from networth.storage import migrate
from networth.store import Store
from networth.tokenstore import Secret, inspect_presence
from tests.test_query import (
    NOW,
    SOURCE_AS_OF,
    add_account,
    add_item,
    add_observation,
    add_run,
)

FLOW = "a" * 32
RESULT = "b" * 32
REF = f"link-token.{FLOW}"


@pytest.fixture
def runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[sqlite3.Connection, Path]]:
    monkeypatch.setenv("NETWORTH_ENV", "sandbox")
    original = environment.paths_for
    monkeypatch.setattr(
        "networth.commands._read.paths_for",
        lambda env: original(env, secrets_dir=tmp_path, data_dir=tmp_path),
    )
    path = tmp_path / "networth-sandbox.db"
    db = sqlite3.connect(path)
    migrate(db)
    try:
        yield db, path
    finally:
        db.close()


def report(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)  # type: ignore[no-any-return]


def snapshot(db: sqlite3.Connection, *, unknown: bool = False) -> int:
    item = add_item(db, "read-cli")
    account = add_account(db, "read-cli", item_id=item, policy=FreshnessPolicy.SYNCED_BALANCE)
    add_run(db, "run-cli")
    add_observation(
        Store(db),
        "run-cli",
        account,
        12_345,
        source_as_of=None if unknown else SOURCE_AS_OF,
        source_clock="UNKNOWN" if unknown else "SYNTHETIC_CLOCK",
    )
    Snapshotter(Store(db)).run("run-cli", at=NOW)
    db.commit()
    return account


@pytest.mark.parametrize("unknown", [False, True])
def test_show_binds_total_to_its_source_age_not_fetch_or_wall_clock(
    runtime: tuple[sqlite3.Connection, Path],
    capsys: pytest.CaptureFixture[str],
    unknown: bool,
) -> None:
    db, _ = runtime
    snapshot(db, unknown=unknown)
    assert main(["show"]) == 0
    value = report(capsys)
    latest = value["latest"]["snapshot"]
    assert latest["net_worth"]["value_minor"] == 12_345
    assert latest["age"]["state"] == ("UNKNOWN" if unknown else "KNOWN")
    assert latest["net_worth"]["as_of"] == (None if unknown else "2026-01-15T11:00:00Z")
    assert value["freshness_assessed_at"] == "2026-01-15T12:00:00Z"
    assert "not a live sync" in value["freshness_scope"]
    assert value["latest"]["accounts"][0]["freshness"]["state"] == (
        "UNKNOWN" if unknown else "FRESH"
    )


def test_static_only_total_has_no_fabricated_date(
    runtime: tuple[sqlite3.Connection, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    db, _ = runtime
    # An empty contributing age basis is also STATIC_ONLY, never NOW.
    add_run(db, "empty")
    Snapshotter(Store(db)).run("empty", at=NOW)
    db.commit()
    assert main(["show"]) == 0
    value = report(capsys)["latest"]["snapshot"]
    assert value["age"]["state"] == "STATIC_ONLY"
    assert value["net_worth"]["as_of"] is None


def test_mismatch_prints_no_headline_but_doctor_keeps_diagnostics(
    runtime: tuple[sqlite3.Connection, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    db, _ = runtime
    snapshot(db)
    add_account(db, "added", item_id=None, policy=FreshnessPolicy.MANUAL_STATIC)
    db.commit()
    assert main(["show"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "new successful snapshot" in captured.err
    assert main(["doctor"]) == 1
    value = report(capsys)
    assert "MISMATCH" in value["snapshot"]
    assert "new successful snapshot" in value["snapshot"]
    assert len(value["accounts"]) == 2
    assert "last_successful_backup" in value
    assert "net_worth" not in value


def test_history_keeps_ages_and_follows_relinked_lineage(
    runtime: tuple[sqlite3.Connection, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    db, _ = runtime
    first = snapshot(db)
    db.execute(
        "UPDATE account SET reconciliation_state='ARCHIVED', archived_at=? WHERE id=?",
        (NOW.isoformat(), first),
    )
    new_item = add_item(db, "replacement")
    second = add_account(
        db, "replacement", item_id=new_item, policy=FreshnessPolicy.SYNCED_BALANCE, lineage_id=first
    )
    add_run(db, "second", at=NOW + timedelta(days=1))
    add_observation(Store(db), "second", second, 12_400, observed_at=NOW + timedelta(days=1))
    db.commit()
    assert main(["history", "--account", str(second)]) == 0
    values = report(capsys)["history"]
    assert [v["account_id"] for v in values] == [first, second]
    assert all(v["figure"]["as_of"] == "2026-01-15T11:00:00Z" for v in values)
    assert main(["history"]) == 0
    totals = report(capsys)["history"]
    assert len(totals) == 1
    assert totals[0]["age"]["state"] == "KNOWN"


def test_backup_publication_and_restore_evidence_are_distinct(
    runtime: tuple[sqlite3.Connection, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db, _ = runtime
    account = snapshot(db)
    db.execute(
        "UPDATE account SET last_fetch_at=?,last_source_as_of=? WHERE id=?",
        (NOW.isoformat(), SOURCE_AS_OF.isoformat(), account),
    )
    db.execute(
        "INSERT INTO backup_archive(archive_id,built_at,archive_sha256,byte_size,manifest_sha256) "
        "VALUES (?,?,?,?,?)",
        ("c" * 32, NOW.isoformat(), "0" * 64, 1, "1" * 64),
    )
    db.commit()
    output, _ = read_doctor(db, tmp_path, NOW)
    emit(output)
    assert report(capsys)["last_successful_backup"]["at"] is None
    stamp = (NOW - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    db.execute(
        "UPDATE backup_archive SET pulled_verified_at=?, pulled_by=?",
        (stamp, "zelengs-macbook-air-2"),
    )
    db.execute(
        "UPDATE backup_state SET last_verified_restore_at=?, key_escrow_confirmed_at=?, "
        "probe_refusal_count=4,dispatch_rejection_count=7",
        (stamp, stamp),
    )
    db.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) VALUES ('test',?,'test','ACTIVE')",
        (stamp,),
    )
    db.execute(
        "INSERT INTO publication(snapshot_id,pairing_id,seq,schema_version,published_at) "
        "VALUES (1,'test','1','1',?)",
        (stamp,),
    )
    db.execute(
        "UPDATE daemon_state SET publish_epoch=3,epoch_bumped_at=?,epoch_bumped_reason='restore'",
        (stamp,),
    )
    output, status = read_doctor(db, tmp_path, NOW)
    assert status == 0
    emit(output)
    value = report(capsys)
    assert value["last_successful_backup"]["at"] == stamp
    assert value["last_verified_restore"]["days_since"] == 3
    assert value["last_successful_publication"]["age_seconds"] == 3 * 86400
    assert "attestation" in value["key_escrow_confirmed_at"]["evidence"]
    assert value["probe_refusal_count"] == 4
    assert value["dispatch_rejection_count"] == 7
    assert value["restore_lineage_diagnostic"][0]["publish_epoch"] == 3
    assert value["accounts"][0]["last_fetch_at"] != value["accounts"][0]["last_source_as_of"]


def link(db: sqlite3.Connection, *, closed: bool = False) -> None:
    stamp = NOW.isoformat()
    db.execute(
        "INSERT INTO link_request(flow_id,secret_ref,minted_at,hosted_url_expires_at,state,"
        "polling_closed_at) VALUES (?,?,?,?,'URL_MINTED',?)",
        (FLOW, REF, stamp, stamp, stamp if closed else None),
    )
    db.execute(
        "INSERT INTO link_session(flow_id,link_session_id,state,finished_at) "
        "VALUES (?,'synthetic-session','SESSION_EXITED',?)",
        (FLOW, stamp),
    )
    db.execute(
        "INSERT INTO link_result(result_id,flow_id,link_session_id,token_digest,state,"
        "finished_at,token_exchange_expires_at,session_retention_expires_at,exchange_attempts) "
        "VALUES (?,?,'synthetic-session',?,'EXCHANGE_UNCERTAIN',?,?,?,1)",
        (RESULT, FLOW, "d" * 64, stamp, stamp, stamp),
    )
    db.execute(
        "INSERT INTO link_result_attempt(result_id,attempt_number,request_id) VALUES (?,1,NULL)",
        (RESULT,),
    )
    db.commit()


def test_lost_exchange_response_still_produces_an_honest_support_ticket(
    runtime: tuple[sqlite3.Connection, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db, _ = runtime
    link(db)
    output, _ = read_doctor(db, tmp_path, NOW)
    emit(output)
    value = report(capsys)
    flow = value["link_flows"][0]
    assert flow["flow_id"] == {"status": "present", "value": FLOW}
    result = flow["results"][0]
    assert result["link_session_id"]["value"] == "synthetic-session"
    assert result["item_id"] == {"status": "never observed", "value": None}
    assert result["attempts"][0]["request_id"] == {"status": "never observed", "value": None}
    assert result["state"] == "EXCHANGE_UNCERTAIN"
    assert value["item_budget"]["remaining"] == 9
    db.execute("UPDATE link_result_attempt SET request_id=?", ("synthetic-request",))
    output, _ = read_doctor(db, tmp_path, NOW)
    emit(output)
    result = report(capsys)["link_flows"][0]["results"][0]
    assert result["attempts"][0]["request_id"]["value"] == "synthetic-request"


@pytest.mark.parametrize("closed", [False, True])
def test_reaper_partial_states_show_pending_material_and_dangling_reference(
    runtime: tuple[sqlite3.Connection, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    closed: bool,
) -> None:
    db, _ = runtime
    link(db, closed=closed)
    marker = "SYNTHETIC-SECRET-NEVER-PRINT"
    pending = tmp_path / f".{REF}.pending"
    pending.write_text(marker)
    before = pending.stat()
    output, _ = read_doctor(db, tmp_path, NOW)
    emit(output)
    captured = capsys.readouterr()
    assert marker not in captured.out
    flow = json.loads(captured.out)["link_flows"][0]
    assert flow["material_presence"] == {"published": "ABSENT", "pending": "PRESENT"}
    assert flow["dangling_secret_ref"] is True
    assert flow["reaping"]["overdue_material"] is closed
    assert pending.read_text() == marker
    assert pending.stat().st_mtime_ns == before.st_mtime_ns
    # Clearing the ref cannot hide retained deterministic material.
    db.execute("UPDATE link_request SET secret_ref=NULL")
    output, _ = read_doctor(db, tmp_path, NOW)
    emit(output)
    assert report(capsys)["link_flows"][0]["reaping"]["overdue_material"] is closed


def test_retention_and_holds_do_not_become_a_reap_deadline(
    runtime: tuple[sqlite3.Connection, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db, _ = runtime
    link(db, closed=True)
    (tmp_path / f"{REF}.json").write_text("synthetic material")
    db.execute(
        "UPDATE link_result SET session_retention_expires_at=?",
        ((NOW + timedelta(hours=1)).isoformat(),),
    )
    output, _ = read_doctor(db, tmp_path, NOW)
    emit(output)
    assert report(capsys)["link_flows"][0]["reaping"]["status"] == "RETAIN_UNTIL_DEADLINE"
    db.execute(
        "INSERT INTO link_material_hold(hold_id,flow_id,reason,observed_at) "
        "VALUES ('hold',?,'UNVERIFIED_MATERIAL',?)",
        (FLOW, NOW.isoformat()),
    )
    output, status = read_doctor(db, tmp_path, NOW + timedelta(days=1))
    assert status == 1
    emit(output)
    value = report(capsys)
    assert value["item_budget"]["remaining"] is None
    assert value["link_flows"][0]["reaping"]["status"] == "HELD_OR_UNKNOWN_DEADLINE"


def test_access_denial_and_symlink_are_not_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / f"{REF}.json").symlink_to(tmp_path / "missing")
    assert inspect_presence(tmp_path, REF)["published"] == "UNKNOWN"

    def denied(self: Path) -> Any:
        raise PermissionError("synthetic secret must not be echoed")

    monkeypatch.setattr(Path, "lstat", denied)
    assert set(inspect_presence(tmp_path, REF).values()) == {"UNKNOWN"}


def test_commands_do_not_write_even_with_an_active_wal_writer(
    runtime: tuple[sqlite3.Connection, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    db, path = runtime
    snapshot(db)
    before = db.serialize()
    db.execute("BEGIN IMMEDIATE")
    db.execute("UPDATE daemon_state SET publish_epoch=99")
    for verb in ("show", "history", "doctor"):
        assert main([verb]) == 0
        value = report(capsys)
        if verb == "doctor":
            assert value["restore_lineage_diagnostic"][0]["publish_epoch"] == 0
    db.rollback()
    assert db.serialize() == before
    with database(path) as reader:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.execute("DELETE FROM snapshot")
        assert NetWorthQuery(Store(reader)).latest() is not None


def test_missing_database_is_not_created_and_schema_is_not_migrated(
    tmp_path: Path,
    runtime: tuple[sqlite3.Connection, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "absent.db"
    with pytest.raises(sqlite3.OperationalError), database(missing):
        pass
    assert not missing.exists()
    db, _ = runtime
    db.execute("PRAGMA user_version=1")
    db.commit()
    assert main(["show"]) == 2
    assert "No repair or migration" in capsys.readouterr().err
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_local_doctor_is_separate_and_retains_expired_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("NETWORTH_ENV", raising=False)
    monkeypatch.setenv("NETWORTH_LINK_RECOVERY_DIR", str(tmp_path / "records"))
    record = RecoveryRecord(FLOW, Secret("synthetic-secret"), NOW, None, 1800, NOW)
    directory = tmp_path / "records"
    store_and_verify(directory, record, holder="zelengs-macbook-air-2", now=NOW)
    before = (directory / f"{FLOW}.json").read_bytes()
    assert main(["doctor", "--local"]) == 0
    value = report(capsys)
    assert value["records"][0]["reap_after"] == "2026-01-15T12:00:00Z"
    assert value["records"][0]["overdue"] is True
    assert "synthetic-secret" not in json.dumps(value)
    assert "item_budget" not in value
    assert (directory / f"{FLOW}.json").read_bytes() == before
    assert main(["show"]) == 2
    assert "NETWORTH_ENV is required" in capsys.readouterr().err


def test_real_worker_loses_exchange_response_then_doctor_retains_ticket(
    runtime: tuple[sqlite3.Connection, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from networth.link_worker import run_request
    from networth.tokenstore import SecretKind, TokenStore
    from tests.test_link_worker import FLOW as worker_flow
    from tests.test_link_worker import LINK, PUBLIC, Client, Crash
    from tests.test_link_worker import NOW as worker_now

    db, _ = runtime
    tokens = TokenStore(tmp_path / "synthetic-worker-tokens")
    ref = tokens.put(SecretKind.LINK_TOKEN, worker_flow, LINK)
    stamp = worker_now.isoformat()
    db.execute(
        "INSERT INTO link_request(flow_id,secret_ref,minted_at,hosted_url_expires_at,state) "
        "VALUES (?,?,?,?,'URL_MINTED')",
        (worker_flow, ref, stamp, stamp),
    )
    db.commit()
    client = Client()
    client.exchange_error = Crash()
    with pytest.raises(Crash):
        run_request(
            db, tokens, client, flow_id=worker_flow, country_codes=("US",), clock=lambda: worker_now
        )
    # Restart classifies the already-sent request without spending another send.
    client.exchange_error = None
    run_request(
        db, tokens, client, flow_id=worker_flow, country_codes=("US",), clock=lambda: worker_now
    )
    assert client.exchanges == [PUBLIC]
    output, _ = read_doctor(db, tokens.directory, worker_now)
    emit(output)
    captured = capsys.readouterr()
    assert LINK not in captured.out and PUBLIC not in captured.out
    flow = json.loads(captured.out)["link_flows"][0]
    result = flow["results"][0]
    assert flow["flow_id"]["value"] == worker_flow
    assert result["state"] == "EXCHANGE_UNCERTAIN"
    assert result["link_session_id"]["value"] == "synthetic-session"
    assert result["item_id"] == {"status": "never observed", "value": None}
    assert result["attempts"][0]["request_id"] == {"status": "never observed", "value": None}


def test_local_unreadable_record_is_unknown_and_is_not_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("NETWORTH_LINK_RECOVERY_DIR", str(tmp_path))
    path = tmp_path / f"{FLOW}.json"
    path.write_text("SYNTHETIC_SECRET_NOT_JSON")
    assert main(["doctor", "--local"]) == 1
    value = report(capsys)
    assert value["records"][0]["reap_after"] is None
    assert value["records"][0]["status"].startswith("UNREADABLE")
    assert "SYNTHETIC_SECRET" not in json.dumps(value)
    assert path.read_text() == "SYNTHETIC_SECRET_NOT_JSON"


def test_read_transaction_keeps_one_snapshot_across_concurrent_commit(
    runtime: tuple[sqlite3.Connection, Path],
) -> None:
    writer, path = runtime
    snapshot(writer)
    with database(path) as reader:
        before = NetWorthQuery(Store(reader)).latest()
        add_account(writer, "concurrent", item_id=None, policy=FreshnessPolicy.MANUAL_STATIC)
        writer.commit()
        assert NetWorthQuery(Store(reader)).latest() == before


def test_future_diagnostic_clock_is_not_zero_age(
    runtime: tuple[sqlite3.Connection, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db, _ = runtime
    db.execute(
        "UPDATE backup_state SET last_verified_restore_at=?",
        ((NOW + timedelta(days=1)).isoformat().replace("+00:00", "Z"),),
    )
    output, _ = read_doctor(db, tmp_path, NOW)
    emit(output)
    value = report(capsys)["last_verified_restore"]
    assert value["status"] == "FUTURE_CLOCK"
    assert value["days_since"] is None
    assert value["age_seconds"] < 0
