"""Mac-side atomic receipt, write-back retry, battery evidence, and canary."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from networth.backup.archive import ArchiveKind, BackupBuilder, ProbeOutcome
from networth.backup.crypto import AuthenticationError
from networth.backup.puller import (
    CURRENT_RECEIPT,
    PENDING_REPORTS,
    PULL_JOURNAL,
    BackupPuller,
    PowerSource,
    read_power_source,
    run_canary,
)
from networth.backup.state import BackupStateStore, open_database
from networth.backup.transport import FetchedArchive, RemoteProbe, TransportError
from networth.storage import migrate
from networth.tokenstore import SecretKind, TokenStore, new_flow_id

KEY = bytes(range(32))
NOW = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)


class FakeTransport:
    def __init__(self, current: bytes, archive_id: str, probe: bytes | None = None) -> None:
        self.current = current
        self.archive_id = archive_id
        self.probe = current if probe is None else probe
        self.transfer = True
        self.fail_pull_records = 0
        self.pull_records: list[tuple[str, str]] = []
        self.drill_records: list[tuple[str, str]] = []
        self.probes: list[RemoteProbe] = []

    def fetch_archive(self, kind: ArchiveKind, destination: Path) -> FetchedArchive | None:
        if not self.transfer:
            return None
        destination.write_bytes(self.current if kind is ArchiveKind.CURRENT else self.probe)
        return FetchedArchive(self.archive_id if kind is ArchiveKind.CURRENT else None)

    def build_probe(self) -> RemoteProbe:
        if not self.probes:
            raise AssertionError("no fake probe response remains")
        return self.probes.pop(0)

    def record_pull(self, archive_id: str, verdict: str) -> None:
        self.pull_records.append((archive_id, verdict))
        if self.fail_pull_records:
            self.fail_pull_records -= 1
            raise TransportError("injected write-back loss")

    def record_drill(self, archive_id: str, verdict: str) -> None:
        self.drill_records.append((archive_id, verdict))


def _archives(tmp_path: Path) -> tuple[bytes, bytes, str, int]:
    database = tmp_path / "source.db"
    connection = sqlite3.connect(database)
    migrate(connection)
    tokens = TokenStore(tmp_path / "source-tokens")
    ref = tokens.put(
        SecretKind.ACCESS_TOKEN,
        new_flow_id(),
        "synthetic-puller-material",
        item_id="synthetic-item",
    )
    connection.execute(
        "INSERT INTO institution(plaid_institution_id, name, is_oauth) "
        "VALUES ('synthetic-institution', 'Synthetic institution', 0)"
    )
    connection.execute(
        "INSERT INTO item(institution_id, plaid_item_id, secret_ref, status, "
        "status_since, created_at) VALUES (1, 'synthetic-item', ?, 'HEALTHY', ?, ?)",
        (ref, "2026-09-07T10:00:00Z", "2026-09-07T10:00:00Z"),
    )
    connection.commit()
    connection.close()
    builder = BackupBuilder(
        database=database,
        token_store=tokens,
        archive_dir=tmp_path / "remote",
        backup_key=KEY,
    )
    current = builder.build_current(now=NOW)
    probe = builder.build_probe(now=NOW)
    return (
        current.path.read_bytes(),
        probe.path.read_bytes(),
        current.archive_id,
        probe.probe_generation,
    )


def test_verified_pull_is_atomic_records_battery_and_writes_back(tmp_path: Path) -> None:
    current, probe, archive_id, _ = _archives(tmp_path)
    transport = FakeTransport(current, archive_id, probe)
    destination = tmp_path / "mac-copy"
    puller = BackupPuller(
        transport=transport,
        destination=destination,
        backup_key=KEY,
        clock=lambda: NOW,
        power_reader=lambda: PowerSource.BATTERY,
    )
    result = puller.run_once()
    assert result.archive_id == archive_id
    assert result.transferred
    assert result.report_recorded
    assert result.power_source is PowerSource.BATTERY
    assert (destination / "current.nwb").read_bytes() == current
    assert json.loads((destination / CURRENT_RECEIPT).read_text()) == {
        "archive_id": archive_id,
        "archive_sha256": hashlib.sha256(current).hexdigest(),
    }
    assert transport.pull_records == [(archive_id, "VERIFIED")]
    assert json.loads((destination / PENDING_REPORTS).read_text()) == []
    journal = [json.loads(line) for line in (destination / PULL_JOURNAL).read_text().splitlines()]
    assert journal == [
        {
            "archive_id": archive_id,
            "power_source": "BATTERY",
            "recorded": True,
            "run_at": NOW.isoformat(),
            "transferred": True,
            "verified": True,
        }
    ]


def test_failed_write_back_stays_pending_and_retries_without_transfer(tmp_path: Path) -> None:
    current, probe, archive_id, _ = _archives(tmp_path)
    transport = FakeTransport(current, archive_id, probe)
    transport.fail_pull_records = 1
    destination = tmp_path / "mac-copy"
    puller = BackupPuller(
        transport=transport,
        destination=destination,
        backup_key=KEY,
        clock=lambda: NOW,
        power_reader=lambda: PowerSource.AC,
    )
    first = puller.run_once()
    assert not first.report_recorded
    pending = json.loads((destination / PENDING_REPORTS).read_text())
    assert pending == [{"archive_id": archive_id, "kind": "pull", "verdict": "VERIFIED"}]

    transport.transfer = False
    second = puller.run_once()
    assert not second.transferred
    assert second.report_recorded
    assert json.loads((destination / PENDING_REPORTS).read_text()) == []
    # First failure, retry-before-fetch, and the ordinary re-record of the
    # already-held verified archive. The last two happen with no transfer.
    assert transport.pull_records == [
        (archive_id, "VERIFIED"),
        (archive_id, "VERIFIED"),
        (archive_id, "VERIFIED"),
    ]


def test_failed_write_back_leaves_the_vps_row_null_until_a_later_retry(tmp_path: Path) -> None:
    current, probe, archive_id, _ = _archives(tmp_path)
    database = tmp_path / "source.db"

    class StateTransport(FakeTransport):
        def record_pull(self, selected_archive_id: str, verdict: str) -> None:
            self.pull_records.append((selected_archive_id, verdict))
            if self.fail_pull_records:
                self.fail_pull_records -= 1
                raise TransportError("injected loss before VPS commit")
            with closing(open_database(database)) as connection:
                BackupStateStore(connection).record_pull(
                    archive_id=selected_archive_id,
                    verdict=verdict,
                    pulled_by="zelengs-macbook-air-2",
                    at=NOW,
                )
                connection.commit()

    transport = StateTransport(current, archive_id, probe)
    transport.fail_pull_records = 1
    puller = BackupPuller(
        transport=transport,
        destination=tmp_path / "mac-copy",
        backup_key=KEY,
        clock=lambda: NOW,
        power_reader=lambda: PowerSource.BATTERY,
    )
    assert not puller.run_once().report_recorded
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT pulled_verified_at FROM backup_archive WHERE archive_id = ?",
            (archive_id,),
        ).fetchone() == (None,)
    finally:
        connection.close()

    transport.transfer = False
    assert puller.run_once().report_recorded
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT pulled_verified_at, pulled_by FROM backup_archive WHERE archive_id = ?",
            (archive_id,),
        ).fetchone() == ("2026-09-07T10:00:00.000000Z", "zelengs-macbook-air-2")
    finally:
        connection.close()


def test_failed_destination_verification_is_distinct_from_a_pull_that_never_ran(
    tmp_path: Path,
) -> None:
    current, probe, archive_id, _ = _archives(tmp_path)
    database = tmp_path / "source.db"

    class StateTransport(FakeTransport):
        def record_pull(self, selected_archive_id: str, verdict: str) -> None:
            self.pull_records.append((selected_archive_id, verdict))
            with closing(open_database(database)) as connection:
                BackupStateStore(connection).record_pull(
                    archive_id=selected_archive_id,
                    verdict=verdict,
                    pulled_by="zelengs-macbook-air-2",
                    at=NOW,
                )
                connection.commit()

    corrupt = current[:-1] + bytes((current[-1] ^ 1,))
    transport = StateTransport(corrupt, archive_id, probe)
    puller = BackupPuller(
        transport=transport,
        destination=tmp_path / "mac-copy",
        backup_key=KEY,
        clock=lambda: NOW,
        power_reader=lambda: PowerSource.BATTERY,
    )
    with pytest.raises(AuthenticationError):
        puller.run_once()

    assert transport.pull_records == [(archive_id, "FAILED")]
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT pulled_verified_at, verify_error FROM backup_archive WHERE archive_id = ?",
            (archive_id,),
        ).fetchone() == (None, "destination verification failed")


def test_bad_download_never_replaces_the_last_verified_copy(tmp_path: Path) -> None:
    current, probe, archive_id, _ = _archives(tmp_path)
    transport = FakeTransport(current, archive_id, probe)
    destination = tmp_path / "mac-copy"
    puller = BackupPuller(
        transport=transport,
        destination=destination,
        backup_key=KEY,
        clock=lambda: NOW,
        power_reader=lambda: PowerSource.UNKNOWN,
    )
    puller.run_once()
    transport.current = current[:-1] + bytes((current[-1] ^ 1,))
    with pytest.raises(AuthenticationError):
        puller.run_once()
    assert (destination / "current.nwb").read_bytes() == current
    assert not list(destination.glob(".tmp-pull-*"))
    last = json.loads((destination / PULL_JOURNAL).read_text().splitlines()[-1])
    assert last["verified"] is False
    assert last["power_source"] == "UNKNOWN"
    assert last["recorded"] is True
    assert transport.pull_records[-1] == (archive_id, "FAILED")


def test_failure_after_durable_verification_does_not_falsify_the_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current, probe, archive_id, _ = _archives(tmp_path)
    destination = tmp_path / "mac-copy"
    puller = BackupPuller(
        transport=FakeTransport(current, archive_id, probe),
        destination=destination,
        backup_key=KEY,
        clock=lambda: NOW,
        power_reader=lambda: PowerSource.AC,
    )

    def fail_to_queue_report(report: object) -> None:
        del report
        raise RuntimeError("injected local report failure")

    monkeypatch.setattr(puller.state, "add_pending", fail_to_queue_report)
    with pytest.raises(RuntimeError, match="local report failure"):
        puller.run_once()

    assert (destination / "current.nwb").read_bytes() == current
    last = json.loads((destination / PULL_JOURNAL).read_text().splitlines()[-1])
    assert last["verified"] is True
    assert last["recorded"] is False


def test_pending_state_failure_still_journals_measured_power(tmp_path: Path) -> None:
    current, probe, archive_id, _ = _archives(tmp_path)
    destination = tmp_path / "mac-copy"
    puller = BackupPuller(
        transport=FakeTransport(current, archive_id, probe),
        destination=destination,
        backup_key=KEY,
        clock=lambda: NOW,
        power_reader=lambda: PowerSource.BATTERY,
    )
    (destination / PENDING_REPORTS).write_text("{", encoding="utf-8")

    with pytest.raises(RuntimeError, match="pending backup report state"):
        puller.run_once()

    last = json.loads((destination / PULL_JOURNAL).read_text().splitlines()[-1])
    assert last["power_source"] == "BATTERY"
    assert last["verified"] is False


@pytest.mark.parametrize("clock_skew", [timedelta(hours=-1), timedelta(hours=1)])
def test_canary_verdict_ignores_mac_clock_skew(tmp_path: Path, clock_skew: timedelta) -> None:
    current, probe, archive_id, generation = _archives(tmp_path)
    transport = FakeTransport(current, archive_id, probe)
    transport.probes = [RemoteProbe(generation, ProbeOutcome.BUILT)]
    observed = NOW + clock_skew
    result = run_canary(
        transport=transport,
        destination=tmp_path / f"canary-{clock_skew.total_seconds()}",
        backup_key=KEY,
        local_observed_at=observed,
    )
    assert result.probe_generation == generation
    assert result.local_observed_at == observed
    assert not list((tmp_path / f"canary-{clock_skew.total_seconds()}").glob("*probe*"))


def test_canary_waits_out_reuse_and_requires_its_own_built_generation(tmp_path: Path) -> None:
    current, probe, archive_id, generation = _archives(tmp_path)
    transport = FakeTransport(current, archive_id, probe)
    transport.probes = [
        RemoteProbe(generation - 1, ProbeOutcome.REUSED),
        RemoteProbe(generation, ProbeOutcome.BUILT),
    ]
    waits: list[float] = []
    result = run_canary(
        transport=transport,
        destination=tmp_path / "canary",
        backup_key=KEY,
        local_observed_at=NOW,
        wait=waits.append,
    )
    assert result.probe_generation == generation
    assert waits == [60.0]


def test_canary_rejects_a_different_generation(tmp_path: Path) -> None:
    current, probe, archive_id, generation = _archives(tmp_path)
    transport = FakeTransport(current, archive_id, probe)
    transport.probes = [RemoteProbe(generation + 1, ProbeOutcome.BUILT)]
    with pytest.raises(RuntimeError, match="different"):
        run_canary(
            transport=transport,
            destination=tmp_path / "canary",
            backup_key=KEY,
            local_observed_at=NOW,
        )


@pytest.mark.parametrize(
    "pmset_line,expected",
    [
        ("Now drawing from 'Battery Power'", PowerSource.BATTERY),
        ("Now drawing from 'AC Power'", PowerSource.AC),
        ("Now drawing from 'UPS Power'", PowerSource.UPS),
    ],
)
def test_power_source_is_read_from_pmset_each_time(
    monkeypatch: pytest.MonkeyPatch, pmset_line: str, expected: PowerSource
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, pmset_line + "\n", "")

    monkeypatch.setattr("networth.backup.puller.subprocess.run", run)
    assert read_power_source() is expected
    assert calls == [["pmset", "-g", "batt"]]
