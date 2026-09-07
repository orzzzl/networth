"""Offline restore and the five independent §9.3a acceptance cases."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from networth.backup.archive import BackupBuilder
from networth.backup.drill import run_restore_drill
from networth.backup.puller import PENDING_REPORTS
from networth.backup.replay import PairingScopedGuard, ReplayVerdict, envelope, run_replay_drill
from networth.backup.restore import RestoreError, restore_archive, restored_files_are_private
from networth.backup.transport import RemoteProbe, TransportError
from networth.storage import migrate
from networth.tokenstore import SecretKind, TokenStore, new_flow_id

KEY = bytes(range(32))
NOW = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)


def _archive(tmp_path: Path, *, orphan: bool = False) -> Path:
    database = tmp_path / "source.db"
    connection = sqlite3.connect(database)
    migrate(connection)
    tokens = TokenStore(tmp_path / "source-tokens")
    ref = tokens.put(
        SecretKind.ACCESS_TOKEN,
        new_flow_id(),
        "synthetic-restore-material",
        item_id="synthetic-item",
    )
    if orphan:
        tokens.put(
            SecretKind.ACCESS_TOKEN,
            new_flow_id(),
            "synthetic-orphan-material",
            item_id="synthetic-orphan",
        )
    connection.execute(
        "INSERT INTO institution(plaid_institution_id, name, is_oauth) "
        "VALUES ('synthetic-institution', 'Synthetic institution', 0)"
    )
    connection.execute(
        """
        INSERT INTO item(
            institution_id, plaid_item_id, secret_ref, status, status_since, created_at
        ) VALUES (1, 'synthetic-item', ?, 'HEALTHY', ?, ?)
        """,
        (ref, "2026-09-07T09:00:00Z", "2026-09-07T09:00:00Z"),
    )
    connection.execute(
        "INSERT INTO pairing(id, created_at, key_ref, state) "
        "VALUES ('pairing-old', ?, 'payload-key-old', 'ACTIVE')",
        ("2026-09-07T09:00:00Z",),
    )
    connection.execute(
        'INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok) '
        "VALUES ('run', ?, ?, 'TEST', 1)",
        ("2026-09-07T09:00:00Z", "2026-09-07T09:00:00Z"),
    )
    connection.execute(
        """
        INSERT INTO snapshot(
            sync_run_id, taken_at, total_net_worth_minor, total_assets_minor,
            total_liabilities_minor, account_count, stale_account_count,
            unknown_freshness_account_count, static_account_count,
            reauth_account_count, unreconciled_account_count, is_complete,
            age_state, as_of, oldest_known_source_as_of
        ) VALUES ('run', ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 'STATIC_ONLY', NULL, NULL)
        """,
        ("2026-09-07T09:00:00Z",),
    )
    connection.execute(
        """
        INSERT INTO publication(snapshot_id, pairing_id, seq, schema_version, published_at, ok)
        VALUES (1, 'pairing-old', 500, '1', ?, 1)
        """,
        ("2026-09-07T09:00:00Z",),
    )
    connection.execute(
        """
        INSERT INTO published_envelope(
            publication_id, pairing_id, schema_version, seq, published_at,
            nonce, ciphertext, is_active
        ) VALUES (1, 'pairing-old', '1', '500', ?, zeroblob(12), zeroblob(16), 1)
        """,
        ("2026-09-07T09:00:00Z",),
    )
    connection.commit()
    connection.close()
    return (
        BackupBuilder(
            database=database,
            token_store=tokens,
            archive_dir=tmp_path / "archives",
            backup_key=KEY,
        )
        .build_current(now=NOW)
        .path
    )


def test_restore_prepares_lineage_drops_old_envelope_and_needs_no_source_host(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    # Everything below runs only against the archive and key. The source paths
    # can disappear, which is the offline criterion rather than a mock network.
    source_database = tmp_path / "source.db"
    source_database.unlink()
    result = restore_archive(archive, KEY, tmp_path / "restored", now=NOW)
    assert result.publish_epoch == 1
    assert result.replay.passed
    assert restored_files_are_private(result)
    connection = sqlite3.connect(result.database)
    try:
        assert connection.execute("SELECT count(*) FROM published_envelope").fetchone() == (0,)
        assert connection.execute(
            "SELECT publish_epoch, epoch_bumped_reason FROM daemon_state WHERE id = 1"
        ).fetchone() == (1, "restore drill")
    finally:
        connection.close()
    assert (
        TokenStore(result.token_store)
        .get(next(path.name.removesuffix(".json") for path in result.token_store.glob("*.json")))
        .reveal()
        == "synthetic-restore-material"
    )


def test_case_1_restore_and_repair_accepts_first_lower_sequence() -> None:
    key = bytes(range(32))
    guard = PairingScopedGuard("P2", key)
    assert guard.receive(envelope("P2", 501, key)) is ReplayVerdict.ACCEPTED


def test_case_2_lower_sequence_within_pairing_is_refused_and_warning_persists() -> None:
    key = bytes(range(32))
    guard = PairingScopedGuard("P2", key)
    assert guard.receive(envelope("P2", 502, key)) is ReplayVerdict.ACCEPTED
    assert guard.receive(envelope("P2", 501, key)) is ReplayVerdict.REFUSED
    assert guard.downgrade_warning
    assert guard.receive(envelope("P2", 502, key)) is ReplayVerdict.UNCHANGED
    assert guard.downgrade_warning
    assert guard.receive(envelope("P2", 503, key)) is ReplayVerdict.ACCEPTED
    assert not guard.downgrade_warning


def test_case_3_pre_restore_pairing_does_not_decrypt() -> None:
    old_key, new_key = bytes(range(32)), bytes(reversed(range(32)))
    guard = PairingScopedGuard("P2", new_key)
    assert guard.receive(envelope("P1", 900, old_key)) is ReplayVerdict.REFUSED


def test_case_4_rollback_without_repair_is_refused() -> None:
    key = bytes(range(32))
    guard = PairingScopedGuard("P2", key, last_seq=700)
    assert guard.receive(envelope("P2", 501, key)) is ReplayVerdict.REFUSED


def test_case_5_same_archive_twice_works_under_two_pairings() -> None:
    result = run_replay_drill(500)
    assert result.same_archive_twice_accepted
    assert result.passed


def test_same_archive_restores_twice_into_independent_destinations(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    first = restore_archive(archive, KEY, tmp_path / "first", now=NOW)
    second = restore_archive(archive, KEY, tmp_path / "second", now=NOW)
    assert first.archive_id == second.archive_id
    assert first.publish_epoch == second.publish_epoch == 1
    assert first.replay.passed and second.replay.passed
    assert first.database.read_bytes() == second.database.read_bytes()


def test_restore_refuses_to_overwrite_any_existing_destination_state(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    destination = tmp_path / "occupied"
    destination.mkdir()
    (destination / "existing").write_text("keep", encoding="utf-8")
    with pytest.raises(RestoreError, match="empty"):
        restore_archive(archive, KEY, destination, now=NOW)
    assert (destination / "existing").read_text(encoding="utf-8") == "keep"


class _DrillTransport:
    def __init__(self, *, fail: bool) -> None:
        self.fail = fail
        self.records: list[tuple[str, str]] = []

    def fetch_archive(self, kind: object, destination: Path) -> bool:
        raise AssertionError("the offline drill must not fetch from the VPS")

    def build_probe(self) -> RemoteProbe:
        raise AssertionError("the offline drill must not build on the VPS")

    def record_pull(self, archive_id: str, verdict: str) -> None:
        raise AssertionError("the drill reports only record-drill")

    def record_drill(self, archive_id: str, verdict: str) -> None:
        self.records.append((archive_id, verdict))
        if self.fail:
            raise TransportError("VPS unreachable")


def test_unreachable_vps_defers_report_but_does_not_turn_a_passing_drill_red(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    local = tmp_path / "mac-copy"
    offline = _DrillTransport(fail=True)
    first = run_restore_drill(
        archive=archive,
        backup_key=KEY,
        local_state_directory=local,
        transport=offline,
        now=NOW,
    )
    assert first.verified
    assert not first.report_recorded
    assert json.loads((local / PENDING_REPORTS).read_text()) == [
        {"archive_id": first.archive_id, "kind": "drill", "verdict": "VERIFIED"}
    ]

    online = _DrillTransport(fail=False)
    second = run_restore_drill(
        archive=archive,
        backup_key=KEY,
        local_state_directory=local,
        transport=online,
        now=NOW,
    )
    assert second.verified and second.report_recorded
    assert json.loads((local / PENDING_REPORTS).read_text()) == []


def test_offline_drill_reports_orphan_tokens_without_failing(tmp_path: Path) -> None:
    archive = _archive(tmp_path, orphan=True)
    result = run_restore_drill(
        archive=archive,
        backup_key=KEY,
        local_state_directory=tmp_path / "mac-copy",
        transport=None,
        now=NOW,
    )
    assert result.verified
    assert result.orphan_token_count == 1
