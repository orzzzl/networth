"""Task 03a's coherent archive, manifest, binding, and probe controls."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from networth.backup.archive import (
    CURRENT_ARCHIVE,
    PROBE_ARCHIVE,
    ArchiveVerificationError,
    BackupBuilder,
    ProbeBusyError,
    ProbeOutcome,
    _length_prefixed,
    verify_archive,
)
from networth.backup.crypto import open_sealed, seal
from networth.storage import migrate
from networth.tokenstore import SecretKind, TokenStore, new_flow_id

NOW = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)
KEY = bytes(range(32))


def _seed(tmp_path: Path, *, orphan: bool = False) -> tuple[Path, TokenStore, sqlite3.Connection]:
    database = tmp_path / "networth.db"
    connection = sqlite3.connect(database)
    migrate(connection)
    tokens = TokenStore(tmp_path / "tokens")
    for number in (1, 2):
        flow_id = new_flow_id()
        item_id = f"synthetic-item-{number}"
        material = f"synthetic-token-material-{number}"
        secret_ref = tokens.put(SecretKind.ACCESS_TOKEN, flow_id, material, item_id=item_id)
        connection.execute(
            "INSERT INTO institution(plaid_institution_id, name, is_oauth) VALUES (?, ?, 0)",
            (f"synthetic-institution-{number}", f"Synthetic institution {number}"),
        )
        connection.execute(
            """
            INSERT INTO item(
                institution_id, plaid_item_id, secret_ref, status, status_since, created_at
            ) VALUES (?, ?, ?, 'HEALTHY', ?, ?)
            """,
            (number, item_id, secret_ref, "2026-09-07T08:00:00Z", "2026-09-07T08:00:00Z"),
        )
    if orphan:
        tokens.put(
            SecretKind.ACCESS_TOKEN,
            new_flow_id(),
            "synthetic-orphan-material",
            item_id="synthetic-orphan",
        )
    connection.commit()
    return database, tokens, connection


def _builder(tmp_path: Path, database: Path, tokens: TokenStore) -> BackupBuilder:
    return BackupBuilder(
        database=database,
        token_store=tokens,
        archive_dir=tmp_path / "archives",
        backup_key=KEY,
    )


def _reseal_with_swapped_material(path: Path) -> None:
    plaintext = open_sealed(path.read_bytes(), KEY)
    members: list[tuple[tarfile.TarInfo, bytes]] = []
    token_indices: list[int] = []
    with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as source:
        for member in source.getmembers():
            extracted = source.extractfile(member)
            assert extracted is not None
            data = extracted.read()
            members.append((member, data))
            if member.name.startswith("tokenstore/") and member.name.endswith(".json"):
                token_indices.append(len(members) - 1)
    assert len(token_indices) == 2
    first_index, second_index = token_indices
    first = json.loads(members[first_index][1])
    second = json.loads(members[second_index][1])
    first["material"], second["material"] = second["material"], first["material"]
    members[first_index] = (members[first_index][0], json.dumps(first).encode())
    members[second_index] = (members[second_index][0], json.dumps(second).encode())

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as destination:
        for original, data in members:
            info = tarfile.TarInfo(original.name)
            info.size = len(data)
            info.mode = original.mode
            info.mtime = original.mtime
            destination.addfile(info, io.BytesIO(data))
    path.write_bytes(seal(output.getvalue(), KEY, nonce=bytes(range(12))))


def test_current_archive_captures_wal_and_authenticates_its_own_evidence(
    tmp_path: Path,
) -> None:
    database, tokens, writer = _seed(tmp_path)
    try:
        # This committed row remains visible through the live WAL while the
        # writer stays open. Copying only networth.db can omit it; VACUUM INTO
        # has to include it.
        writer.execute(
            'INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok) '
            "VALUES ('wal-visible', ?, ?, 'TEST', 1)",
            ("2026-09-07T08:00:00Z", "2026-09-07T08:00:00Z"),
        )
        writer.commit()
        assert Path(f"{database}-wal").exists()

        result = _builder(tmp_path, database, tokens).build_current(now=NOW)
        verified = verify_archive(result.path, KEY)

        assert result.path.name == CURRENT_ARCHIVE
        assert verified.manifest.archive_id == result.archive_id
        assert verified.manifest.schema_version == 3
        assert verified.manifest.db_row_counts["sync_run"] == 1
        assert verified.manifest.item_count == 2
        assert verified.orphan_token_count == 0
        assert result.archive_sha256 not in {"", "0" * 64}
        assert result.archive_sha256 == hashlib.sha256(result.path.read_bytes()).hexdigest()
        assert result.byte_size == result.path.stat().st_size

        row = writer.execute(
            "SELECT archive_sha256, byte_size, manifest_sha256 FROM backup_archive "
            "WHERE archive_id = ?",
            (result.archive_id,),
        ).fetchone()
        assert row == (
            result.archive_sha256,
            result.byte_size,
            hashlib.sha256(result.manifest.canonical_bytes()).hexdigest(),
        )
        # The snapshot precedes the INSERT by contract, so it never contains
        # the row that describes itself.
        assert verified.manifest.db_row_counts["backup_archive"] == 0
        assert writer.execute("SELECT count(*) FROM backup_archive").fetchone() == (1,)
        assert not any(path.name.startswith(".tmp-") for path in result.path.parent.iterdir())
        for material in (b"synthetic-token-material-1", b"synthetic-token-material-2"):
            assert material not in result.path.read_bytes()
    finally:
        writer.close()


def test_orphan_tokens_are_reported_but_do_not_fail_the_drill(tmp_path: Path) -> None:
    database, tokens, connection = _seed(tmp_path, orphan=True)
    try:
        result = _builder(tmp_path, database, tokens).build_current(now=NOW)
        assert verify_archive(result.path, KEY).orphan_token_count == 1
    finally:
        connection.close()


def test_swapping_two_item_tokens_inside_a_freshly_resealed_bundle_fails(tmp_path: Path) -> None:
    database, tokens, connection = _seed(tmp_path)
    try:
        result = _builder(tmp_path, database, tokens).build_current(now=NOW)
        _reseal_with_swapped_material(result.path)
        with pytest.raises(ArchiveVerificationError, match="binding"):
            verify_archive(result.path, KEY)
    finally:
        connection.close()


def test_length_prefixes_distinguish_the_delimiter_collision_shape() -> None:
    assert _length_prefixed("ab") + _length_prefixed("c") != _length_prefixed(
        "a"
    ) + _length_prefixed("bc")


def test_probe_burst_is_one_fixed_file_zero_archive_rows_and_visible_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, tokens, connection = _seed(tmp_path)
    builder = _builder(tmp_path, database, tokens)
    try:
        first = builder.build_probe(now=NOW)
        sequential = [builder.build_probe(now=NOW) for _ in range(49)]
        assert first.outcome is ProbeOutcome.BUILT
        assert {result.outcome for result in sequential} == {ProbeOutcome.REUSED}
        assert {result.probe_generation for result in sequential} == {1}

        real_build = BackupBuilder._build_bytes
        entered = threading.Event()

        def slow_build(self: BackupBuilder, **kwargs: Any) -> Any:
            entered.set()
            time.sleep(0.08)
            return real_build(self, **kwargs)

        monkeypatch.setattr(BackupBuilder, "_build_bytes", slow_build)
        concurrent_now = NOW + timedelta(seconds=61)

        def invoke() -> ProbeOutcome | str:
            try:
                return builder.build_probe(now=concurrent_now).outcome
            except ProbeBusyError:
                return "busy"

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(invoke) for _ in range(10)]
            assert entered.wait(timeout=1)
            concurrent = [future.result() for future in futures]

        assert concurrent.count(ProbeOutcome.BUILT) == 1
        assert set(concurrent) <= {ProbeOutcome.BUILT, ProbeOutcome.REUSED, "busy"}
        assert (tmp_path / "archives" / PROBE_ARCHIVE).is_file()
        assert len([path for path in (tmp_path / "archives").iterdir() if path.is_file()]) == 1
        assert connection.execute("SELECT count(*) FROM backup_archive").fetchone() == (0,)
        state = connection.execute(
            "SELECT probe_generation, probe_refusal_count FROM backup_state WHERE id = 1"
        ).fetchone()
        assert state is not None
        assert state[0] == 2
        assert state[1] >= 49
    finally:
        connection.close()


def test_probe_and_token_writes_use_the_identical_lock_path(tmp_path: Path) -> None:
    database, tokens, connection = _seed(tmp_path)
    builder = _builder(tmp_path, database, tokens)
    assert builder.token_store.lock_path == tokens.directory.parent / ".tokenstore.lock"
    try:
        from networth.filelock import exclusive_file_lock

        entered = threading.Event()
        release = threading.Event()

        def hold_token_lock() -> None:
            with exclusive_file_lock(tokens.lock_path):
                entered.set()
                release.wait(timeout=2)

        holder = threading.Thread(target=hold_token_lock)
        holder.start()
        assert entered.wait(timeout=1)
        with pytest.raises(ProbeBusyError):
            builder.build_probe(now=NOW)
        release.set()
        holder.join(timeout=1)
        assert not holder.is_alive()
    finally:
        release.set()
        connection.close()


def test_losing_probe_cannot_clean_the_active_builders_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, tokens, connection = _seed(tmp_path)
    builder = _builder(tmp_path, database, tokens)
    real_build = BackupBuilder._build_bytes
    entered = threading.Event()
    release = threading.Event()
    active_temporary = builder.archive_dir / ".tmp-active-builder"
    outcome: list[ProbeOutcome] = []
    failure: list[BaseException] = []

    def paused_build(self: BackupBuilder, **kwargs: Any) -> Any:
        active_temporary.write_bytes(b"active")
        entered.set()
        release.wait(timeout=2)
        active_temporary.unlink(missing_ok=True)
        return real_build(self, **kwargs)

    def build_first() -> None:
        try:
            outcome.append(builder.build_probe(now=NOW).outcome)
        except BaseException as exc:
            failure.append(exc)

    monkeypatch.setattr(BackupBuilder, "_build_bytes", paused_build)
    worker = threading.Thread(target=build_first)
    worker.start()
    try:
        assert entered.wait(timeout=1)
        with pytest.raises(ProbeBusyError):
            builder.build_probe(now=NOW)
        assert active_temporary.read_bytes() == b"active"
    finally:
        release.set()
        worker.join(timeout=2)
        connection.close()

    assert not worker.is_alive()
    assert failure == []
    assert outcome == [ProbeOutcome.BUILT]
