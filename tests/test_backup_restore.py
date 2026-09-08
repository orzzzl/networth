"""Offline restore and the five independent §9.3a acceptance cases."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import pytest

from networth.backup.archive import CURRENT_ARCHIVE, BackupBuilder, verify_archive
from networth.backup.crypto import AuthenticationError, open_sealed, seal
from networth.backup.drill import run_restore_drill
from networth.backup.puller import PENDING_REPORTS, CurrentReceipt, LocalBackupState
from networth.backup.restore import RestoreError, restore_archive, restored_files_are_private
from networth.backup.state import BackupStateStore
from networth.backup.transport import FetchedArchive, RemoteProbe, TransportError
from networth.storage import migrate
from networth.tokenstore import SecretKind, TokenStore, new_flow_id

KEY = bytes(range(32))
NOW = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)


class _ReplayVerdict(StrEnum):
    ACCEPTED = "accepted"
    UNCHANGED = "unchanged"
    REFUSED = "refused"


def _envelope(pairing_id: str, seq: int, key: bytes) -> bytes:
    return seal(
        json.dumps(
            {"pairing_id": pairing_id, "seq": seq},
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
        key,
    )


@dataclass(slots=True)
class _PairingScopedGuard:
    """Task-19 model kept in tests; it is not restore evidence."""

    pairing_id: str
    key: bytes
    last_seq: int | None = None
    downgrade_warning: bool = False

    def receive(self, candidate: bytes) -> _ReplayVerdict:
        try:
            raw = json.loads(open_sealed(candidate, self.key))
        except (AuthenticationError, UnicodeDecodeError, json.JSONDecodeError):
            self.downgrade_warning = True
            return _ReplayVerdict.REFUSED
        if not isinstance(raw, dict):
            self.downgrade_warning = True
            return _ReplayVerdict.REFUSED
        pairing_id, seq = raw.get("pairing_id"), raw.get("seq")
        if (
            pairing_id != self.pairing_id
            or not isinstance(seq, int)
            or isinstance(seq, bool)
            or seq <= 0
        ):
            self.downgrade_warning = True
            return _ReplayVerdict.REFUSED
        if self.last_seq is not None and seq < self.last_seq:
            self.downgrade_warning = True
            return _ReplayVerdict.REFUSED
        if self.last_seq == seq:
            return _ReplayVerdict.UNCHANGED
        self.last_seq = seq
        self.downgrade_warning = False
        return _ReplayVerdict.ACCEPTED


def _archive(tmp_path: Path, *, orphan: bool = False, broken_lineage: bool = False) -> Path:
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
    if broken_lineage:
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("UPDATE publication SET pairing_id = 'pairing-missing'")
        connection.execute("UPDATE published_envelope SET pairing_id = 'pairing-missing'")
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
    assert result.preparation.passed
    assert result.preparation.previous_publish_epoch == 0
    assert result.preparation.publication_max_seq_before == 500
    assert result.preparation.publication_max_seq_after == 500
    assert result.preparation.published_envelope_count_before == 1
    assert result.preparation.published_envelope_count_after == 0
    assert result.preparation.pairing_count == 1
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
    guard = _PairingScopedGuard("P2", key)
    assert guard.receive(_envelope("P2", 501, key)) is _ReplayVerdict.ACCEPTED


def test_case_2_lower_sequence_within_pairing_is_refused_and_warning_persists() -> None:
    key = bytes(range(32))
    guard = _PairingScopedGuard("P2", key)
    assert guard.receive(_envelope("P2", 502, key)) is _ReplayVerdict.ACCEPTED
    assert guard.receive(_envelope("P2", 501, key)) is _ReplayVerdict.REFUSED
    assert guard.downgrade_warning
    assert guard.receive(_envelope("P2", 502, key)) is _ReplayVerdict.UNCHANGED
    assert guard.downgrade_warning
    assert guard.receive(_envelope("P2", 503, key)) is _ReplayVerdict.ACCEPTED
    assert not guard.downgrade_warning


def test_case_3_pre_restore_pairing_does_not_decrypt() -> None:
    old_key, new_key = bytes(range(32)), bytes(reversed(range(32)))
    guard = _PairingScopedGuard("P2", new_key)
    assert guard.receive(_envelope("P1", 900, old_key)) is _ReplayVerdict.REFUSED


def test_case_4_rollback_without_repair_is_refused() -> None:
    key = bytes(range(32))
    guard = _PairingScopedGuard("P2", key, last_seq=700)
    assert guard.receive(_envelope("P2", 501, key)) is _ReplayVerdict.REFUSED


def test_case_5_same_archive_twice_works_under_two_pairings() -> None:
    key_three, key_four = os.urandom(32), os.urandom(32)
    third = _PairingScopedGuard("P3", key_three)
    fourth = _PairingScopedGuard("P4", key_four)
    assert third.receive(_envelope("P3", 501, key_three)) is _ReplayVerdict.ACCEPTED
    assert fourth.receive(_envelope("P4", 501, key_four)) is _ReplayVerdict.ACCEPTED


def test_same_archive_restores_twice_into_independent_destinations(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    first = restore_archive(archive, KEY, tmp_path / "first", now=NOW)
    second = restore_archive(archive, KEY, tmp_path / "second", now=NOW)
    assert first.archive_id == second.archive_id
    assert first.publish_epoch == second.publish_epoch == 1
    assert first.preparation.passed and second.preparation.passed
    assert first.database.read_bytes() == second.database.read_bytes()


def test_restore_refuses_to_overwrite_any_existing_destination_state(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    destination = tmp_path / "occupied"
    destination.mkdir()
    (destination / "existing").write_text("keep", encoding="utf-8")
    with pytest.raises(RestoreError, match="empty"):
        restore_archive(archive, KEY, destination, now=NOW)
    assert (destination / "existing").read_text(encoding="utf-8") == "keep"


def test_restore_rejects_broken_pairing_lineage_from_the_actual_database(tmp_path: Path) -> None:
    archive = _archive(tmp_path, broken_lineage=True)
    with pytest.raises(RestoreError, match="foreign-key lineage"):
        restore_archive(archive, KEY, tmp_path / "restored", now=NOW)


class _DrillTransport:
    def __init__(self, *, fail: bool) -> None:
        self.fail = fail
        self.records: list[tuple[str, str]] = []

    def fetch_archive(self, kind: object, destination: Path) -> FetchedArchive | None:
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


def test_failed_drill_reports_failed_using_the_durable_pull_receipt(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    database = tmp_path / "source.db"
    verified = verify_archive(archive, KEY)
    local = LocalBackupState(tmp_path / "mac-copy")
    current = local.directory / CURRENT_ARCHIVE
    original = archive.read_bytes()
    current.write_bytes(original)
    current.chmod(0o600)
    local.replace_current_receipt(
        CurrentReceipt(
            verified.manifest.archive_id,
            hashlib.sha256(original).hexdigest(),
        )
    )
    current.write_bytes(original[:-1] + bytes((original[-1] ^ 1,)))

    class StateDrillTransport(_DrillTransport):
        def record_drill(self, archive_id: str, verdict: str) -> None:
            super().record_drill(archive_id, verdict)
            with sqlite3.connect(database) as connection:
                BackupStateStore(connection).record_drill(
                    archive_id=archive_id,
                    verdict=verdict,
                    at=NOW,
                )

    transport = StateDrillTransport(fail=False)

    result = run_restore_drill(
        archive=current,
        backup_key=KEY,
        local_state_directory=local.directory,
        transport=transport,
        now=NOW,
    )

    assert not result.verified
    assert result.archive_id == verified.manifest.archive_id
    assert result.failure == "AuthenticationError"
    assert result.orphan_token_count is None
    assert not result.restore_preparation_passed
    assert transport.records == [(verified.manifest.archive_id, "FAILED")]
    assert json.loads((local.directory / PENDING_REPORTS).read_text()) == []
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT last_verified_restore_at, last_verified_restore_error "
            "FROM backup_state WHERE id = 1"
        ).fetchone() == (None, "restore verification failed")


def test_drill_rejects_valid_archive_whose_id_disagrees_with_pull_receipt(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    body = archive.read_bytes()
    verified = verify_archive(archive, KEY)
    receipt_archive_id = "f" * 32 if verified.manifest.archive_id != "f" * 32 else "e" * 32
    local = LocalBackupState(tmp_path / "mac-copy")
    current = local.directory / CURRENT_ARCHIVE
    current.write_bytes(body)
    current.chmod(0o600)
    local.replace_current_receipt(
        CurrentReceipt(receipt_archive_id, hashlib.sha256(body).hexdigest())
    )
    transport = _DrillTransport(fail=False)

    result = run_restore_drill(
        archive=current,
        backup_key=KEY,
        local_state_directory=local.directory,
        transport=transport,
        now=NOW,
    )

    assert not result.verified
    assert result.archive_id == receipt_archive_id
    assert result.failure == "ArchiveVerificationError"
    assert transport.records == [(receipt_archive_id, "FAILED")]


def test_drill_rejects_same_id_when_valid_archive_bytes_disagree_with_receipt(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    original = archive.read_bytes()
    verified = verify_archive(archive, KEY)
    resealed = seal(open_sealed(original, KEY), KEY, nonce=b"\xff" * 12)
    assert resealed != original
    local = LocalBackupState(tmp_path / "mac-copy")
    current = local.directory / CURRENT_ARCHIVE
    current.write_bytes(resealed)
    current.chmod(0o600)
    assert verify_archive(current, KEY).manifest.archive_id == verified.manifest.archive_id
    local.replace_current_receipt(
        CurrentReceipt(
            verified.manifest.archive_id,
            hashlib.sha256(original).hexdigest(),
        )
    )
    transport = _DrillTransport(fail=False)

    result = run_restore_drill(
        archive=current,
        backup_key=KEY,
        local_state_directory=local.directory,
        transport=transport,
        now=NOW,
    )

    assert not result.verified
    assert result.archive_id == verified.manifest.archive_id
    assert result.failure == "ArchiveVerificationError"
    assert transport.records == [(verified.manifest.archive_id, "FAILED")]
