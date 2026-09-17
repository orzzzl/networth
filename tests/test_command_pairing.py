"""The task-19a command boundary: commit before QR, and honest revocation copy."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from networth import cli
from networth.commands import pair as pair_command
from networth.pairing import decode_provision, read_payload_key

TAILNET_NAME = "vps.synthetic-tailnet.ts.net"


def test_pair_renders_only_after_the_rotation_is_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "networth.db"
    key_file = tmp_path / "networth-payload.key"
    rendered: list[str] = []

    def render(value: str, *, ansi: bool) -> str:
        assert ansi is False
        provision = decode_provision(value)
        with sqlite3.connect(database) as observer:
            active = observer.execute(
                "SELECT id, key_ref, revoked_at FROM pairing WHERE state = 'ACTIVE'"
            ).fetchone()
            assert active == (
                provision.pairing_id,
                f"payload-key/{provision.pairing_id}",
                None,
            )
            assert observer.execute(
                "SELECT count(*) FROM pairing WHERE state = 'REVOKED' AND revoked_at IS NOT NULL"
            ).fetchone() == (len(rendered),)
            assert observer.execute("SELECT count(*) FROM published_envelope").fetchone() == (0,)
        assert read_payload_key(key_file, key_ref=active[1]) == provision.payload_key
        rendered.append(value)
        return "<synthetic terminal QR>"

    monkeypatch.setattr(pair_command, "render_terminal_qr", render)

    assert (
        cli.main(
            [
                "pair",
                "--database",
                str(database),
                "--payload-key-file",
                str(key_file),
                "--tailnet-name",
                TAILNET_NAME,
            ]
        )
        == 0
    )

    output = capsys.readouterr()
    assert output.err == ""
    assert len(rendered) == 1
    assert "<synthetic terminal QR>" in output.out
    assert rendered[0] in output.out
    assert "cached on a lost or stolen phone is beyond recall" in output.out
    assert "stops future fetches, never past ones" in output.out

    first_pairing = decode_provision(rendered[0]).pairing_id
    assert (
        cli.main(
            [
                "pair",
                "--database",
                str(database),
                "--payload-key-file",
                str(key_file),
                "--tailnet-name",
                TAILNET_NAME,
            ]
        )
        == 0
    )
    assert len(rendered) == 2
    assert decode_provision(rendered[1]).pairing_id != first_pairing
    with sqlite3.connect(database) as observer:
        assert observer.execute(
            "SELECT state, revoked_at IS NOT NULL FROM pairing WHERE id = ?", (first_pairing,)
        ).fetchone() == ("REVOKED", 1)


def test_revoke_drops_the_envelope_and_says_cached_ciphertext_is_beyond_recall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "networth.db"
    key_file = tmp_path / "networth-payload.key"
    monkeypatch.setattr(pair_command, "render_terminal_qr", lambda value, *, ansi: "<QR>")
    arguments = [
        "--database",
        str(database),
        "--payload-key-file",
        str(key_file),
        "--tailnet-name",
        TAILNET_NAME,
    ]
    assert cli.main(["pair", *arguments]) == 0
    capsys.readouterr()

    assert cli.main(["revoke", "--database", str(database)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert "served envelope was dropped in the same commit" in output.out
    assert "cached on a lost or stolen phone is beyond recall" in output.out
    assert "revocation does not reach backwards" in output.out
    with sqlite3.connect(database) as observer:
        assert observer.execute("SELECT state FROM pairing").fetchone() == ("REVOKED",)
        assert observer.execute("SELECT count(*) FROM published_envelope").fetchone() == (0,)


def test_failed_rotation_prints_no_qr_or_typed_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "networth.db"
    key_file = tmp_path / "networth-payload.key"
    monkeypatch.setattr(pair_command, "render_terminal_qr", lambda value, *, ansi: "<QR>")
    arguments = [
        "--database",
        str(database),
        "--payload-key-file",
        str(key_file),
        "--tailnet-name",
        TAILNET_NAME,
    ]
    assert cli.main(["pair", *arguments]) == 0
    original_key = key_file.read_bytes()
    capsys.readouterr()

    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER refuse_pairing_insert
            BEFORE INSERT ON pairing
            BEGIN
                SELECT raise(ABORT, 'synthetic pairing refusal');
            END
            """
        )
        connection.commit()

    assert cli.main(["pair", *arguments]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "synthetic pairing refusal" in output.err
    assert key_file.read_bytes() == original_key
