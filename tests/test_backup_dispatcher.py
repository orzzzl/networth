"""The restricted key's exact four-verb boundary, including every negative."""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

import networth.backup.dispatcher as dispatcher_module
from networth.backup.archive import BackupBuilder
from networth.backup.dispatcher import BackupDispatcher
from networth.storage import migrate
from networth.tokenstore import SecretKind, TokenStore, new_flow_id

KEY = bytes(range(32))
NOW = datetime(2026, 9, 7, 11, 0, tzinfo=UTC)


def _dispatcher(
    tmp_path: Path,
) -> tuple[BackupDispatcher, Path, BackupBuilder, io.BytesIO, io.StringIO]:
    database = tmp_path / "networth.db"
    connection = sqlite3.connect(database)
    migrate(connection)
    tokens = TokenStore(tmp_path / "tokens")
    ref = tokens.put(
        SecretKind.ACCESS_TOKEN,
        new_flow_id(),
        "synthetic-dispatch-material",
        item_id="synthetic-item",
    )
    connection.execute(
        "INSERT INTO institution(plaid_institution_id, name, is_oauth) "
        "VALUES ('synthetic-institution', 'Synthetic institution', 0)"
    )
    connection.execute(
        "INSERT INTO item(institution_id, plaid_item_id, secret_ref, status, "
        "status_since, created_at) VALUES (1, 'synthetic-item', ?, 'HEALTHY', ?, ?)",
        (ref, "2026-09-07T11:00:00Z", "2026-09-07T11:00:00Z"),
    )
    connection.commit()
    connection.close()
    builder = BackupBuilder(
        database=database,
        token_store=tokens,
        archive_dir=tmp_path / "archives",
        backup_key=KEY,
    )
    output, errors = io.BytesIO(), io.StringIO()
    return (
        BackupDispatcher(
            builder=builder,
            database=database,
            stdout=output,
            stderr=errors,
            clock=lambda: NOW,
        ),
        database,
        builder,
        output,
        errors,
    )


def test_four_positive_verbs_and_only_their_declared_writes(tmp_path: Path) -> None:
    dispatcher, database, builder, output, errors = _dispatcher(tmp_path)
    current = builder.build_current(now=NOW)

    assert dispatcher.dispatch("build-probe") == 0
    probe_response = json.loads(output.getvalue())
    assert probe_response == {"outcome": "built", "probe_generation": 1}
    output.seek(0)
    output.truncate()
    assert dispatcher.dispatch("build-probe") == 0
    probe_response = json.loads(output.getvalue())
    assert probe_response == {"outcome": "reused", "probe_generation": 1}
    assert "build-probe cooldown refusal" in errors.getvalue()

    output.seek(0)
    output.truncate()
    assert dispatcher.dispatch("serve-archive current") == 0
    assert output.getvalue() == current.path.read_bytes()

    assert (
        dispatcher.dispatch(f"record-pull {current.archive_id} VERIFIED zelengs-macbook-air-2") == 0
    )
    assert dispatcher.dispatch(f"record-drill {current.archive_id} VERIFIED") == 0

    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT pulled_verified_at, pulled_by, verify_error FROM backup_archive "
            "WHERE archive_id = ?",
            (current.archive_id,),
        ).fetchone() == (
            "2026-09-07T11:00:00.000000Z",
            "zelengs-macbook-air-2",
            None,
        )
        assert connection.execute(
            "SELECT last_verified_restore_at, last_verified_restore_archive_id, "
            "last_verified_restore_error FROM backup_state WHERE id = 1"
        ).fetchone() == (
            "2026-09-07T11:00:00.000000Z",
            current.archive_id,
            None,
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "command,verb",
    [
        (None, "<invalid>"),
        ("bash", "bash"),
        ("networth show", "networth"),
        ("build-archive current", "build-archive"),
        ("record-pull not-an-id VERIFIED zelengs-macbook-air-2", "record-pull"),
        ("serve-archive current;bash", "serve-archive"),
        ("record-drill 00000000000000000000000000000000 VERIFIED extra", "record-drill"),
    ],
)
def test_every_removed_or_malformed_command_is_refused_and_logged(
    tmp_path: Path, command: str | None, verb: str
) -> None:
    dispatcher, database, _, _, errors = _dispatcher(tmp_path)
    assert dispatcher.dispatch(command) != 0
    assert f"verb={verb}" in errors.getvalue()
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT dispatch_rejection_count FROM backup_state WHERE id = 1"
        ).fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM backup_archive").fetchone() == (0,)
    finally:
        connection.close()


def test_well_shaped_archive_id_must_already_name_a_row(tmp_path: Path) -> None:
    dispatcher, database, _, _, errors = _dispatcher(tmp_path)
    missing = "0" * 32
    assert dispatcher.dispatch(f"record-pull {missing} VERIFIED zelengs-macbook-air-2") != 0
    assert "verb=record-pull" in errors.getvalue()
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT count(*) FROM backup_archive").fetchone() == (0,)
        assert connection.execute(
            "SELECT dispatch_rejection_count FROM backup_state WHERE id = 1"
        ).fetchone() == (1,)
    finally:
        connection.close()


def test_dispatcher_source_never_invokes_a_shell() -> None:
    assert dispatcher_module.__file__ is not None
    source = Path(dispatcher_module.__file__).read_text()
    assert "shell=True" not in source
    assert "os.system" not in source
    assert "SSH_ORIGINAL_COMMAND" not in source
