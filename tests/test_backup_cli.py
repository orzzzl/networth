"""CLI wiring for build, refusal counters, truthful age, and wrappers."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from networth import cli
from networth.storage import migrate
from networth.tokenstore import SecretKind, TokenStore, new_flow_id
from tests.conftest import REPO_ROOT

KEY = bytes(range(32))


def _paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    database = tmp_path / "networth.db"
    connection = sqlite3.connect(database)
    migrate(connection)
    tokens = TokenStore(tmp_path / "tokens")
    ref = tokens.put(
        SecretKind.ACCESS_TOKEN,
        new_flow_id(),
        "synthetic-cli-material",
        item_id="synthetic-item",
    )
    connection.execute(
        "INSERT INTO institution(plaid_institution_id, name, is_oauth) "
        "VALUES ('synthetic-institution', 'Synthetic institution', 0)"
    )
    connection.execute(
        "INSERT INTO item(institution_id, plaid_item_id, secret_ref, status, "
        "status_since, created_at) VALUES (1, 'synthetic-item', ?, 'HEALTHY', ?, ?)",
        (ref, "2026-09-07T12:00:00Z", "2026-09-07T12:00:00Z"),
    )
    connection.commit()
    connection.close()
    archive_dir = tmp_path / "archives"
    key_file = tmp_path / "backup.key"
    key_file.write_text(KEY.hex() + "\n", encoding="ascii")
    key_file.chmod(0o600)
    return database, tokens.directory, archive_dir, key_file


def _options(paths: tuple[Path, Path, Path, Path]) -> list[str]:
    database, tokens, archives, key = paths
    return [
        "--database",
        str(database),
        "--token-store",
        str(tokens),
        "--archive-dir",
        str(archives),
        "--key-file",
        str(key),
    ]


def test_doctor_never_promotes_built_at_to_successful_backup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _paths(tmp_path)
    assert cli.main(["backup", "build-archive", "current", *_options(paths)]) == 0
    build = json.loads(capsys.readouterr().out)

    assert cli.main(["backup", "build-probe", *_options(paths)]) == 0
    capsys.readouterr()
    assert cli.main(["backup", "build-probe", *_options(paths)]) == 0
    capsys.readouterr()

    # One disallowed verb increments the other counter.
    old = os.environ.get("SSH_ORIGINAL_COMMAND")
    os.environ["SSH_ORIGINAL_COMMAND"] = "build-archive current"
    try:
        assert cli.main(["backup", "ssh-dispatch", *_options(paths)]) != 0
    finally:
        if old is None:
            os.environ.pop("SSH_ORIGINAL_COMMAND", None)
        else:
            os.environ["SSH_ORIGINAL_COMMAND"] = old
    capsys.readouterr()

    assert cli.main(["backup", "doctor", *_options(paths)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["last_successful_backup"] is None
    assert status["probe_refusal_count"] == 1
    assert status["dispatch_rejection_count"] == 1

    assert (
        cli.main(
            [
                "backup",
                "record-pull",
                build["archive_id"],
                "VERIFIED",
                "zelengs-macbook-air-2",
                *_options(paths),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert cli.main(["backup", "doctor", *_options(paths)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["last_successful_backup"] is not None


def test_shipped_wrappers_are_syntax_clean_and_do_not_evaluate_remote_input() -> None:
    wrappers = [
        REPO_ROOT / "scripts" / "backup-ssh-dispatch",
        REPO_ROOT / "scripts" / "restore-drill.sh",
        REPO_ROOT / "scripts" / "install-backup-puller.sh",
    ]
    assert subprocess.run(["bash", "-n", *map(str, wrappers)], check=False).returncode == 0
    dispatcher = wrappers[0].read_text(encoding="utf-8")
    assert "exec networth backup ssh-dispatch" in dispatcher
    assert "eval " not in dispatcher
    assert "$SSH_ORIGINAL_COMMAND" not in dispatcher


def test_attest_key_records_an_owner_claim_against_a_synthetic_escrow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _paths(tmp_path)
    escrow = tmp_path / "synthetic-offline-escrow"
    escrow.write_text(KEY.hex() + "\n", encoding="ascii")
    escrow.chmod(0o600)
    assert escrow.is_file(), "the test must actually establish its synthetic escrow premise"

    assert cli.main(["backup", "attest-key", *_options(paths)]) == 0
    assert "owner claim" in capsys.readouterr().out
    assert cli.main(["backup", "doctor", *_options(paths)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["key_escrow_confirmed_at"] is not None
    assert status["key_escrow_is_owner_attestation"] is True
