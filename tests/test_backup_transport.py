"""SSH client emits only strings accepted by the forced-command grammar."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from networth.backup.archive import ArchiveKind, ProbeOutcome
from networth.backup.state import ARCHIVE_ID_NOTICE_PREFIX
from networth.backup.transport import SshTransport, TransportError


def test_probe_and_write_back_use_exact_commands_without_a_local_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        output = b'{"outcome":"built","probe_generation":7}\n' if argv[-1] == "build-probe" else b""
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr("networth.backup.transport.subprocess.run", run)
    transport = SshTransport(
        host="tokyo-exit",
        user="networth",
        identity=tmp_path / "networth-backup-ssh.key",
    )
    assert transport.build_probe().outcome is ProbeOutcome.BUILT
    archive_id = "a" * 32
    transport.record_pull(archive_id, "VERIFIED")
    transport.record_drill(archive_id, "FAILED")

    assert [argv[-1] for argv, _ in calls] == [
        "build-probe",
        f"record-pull {archive_id} VERIFIED zelengs-macbook-air-2",
        f"record-drill {archive_id} FAILED",
    ]
    for argv, kwargs in calls:
        assert argv[0] == "ssh"
        assert "--" in argv
        assert "shell" not in kwargs


def test_archive_fetch_streams_to_an_exclusive_private_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"sealed archive bytes"
    archive_id = "a" * 32

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        os.write(kwargs["stdout"], payload)
        notice = f"{ARCHIVE_ID_NOTICE_PREFIX}{archive_id}\n".encode()
        return subprocess.CompletedProcess(argv, 0, b"", notice)

    monkeypatch.setattr("networth.backup.transport.subprocess.run", run)
    transport = SshTransport(
        host="tokyo-exit",
        user="networth",
        identity=tmp_path / "networth-backup-ssh.key",
    )
    destination = tmp_path / "download"
    fetched = transport.fetch_archive(ArchiveKind.CURRENT, destination)
    assert fetched.archive_id == archive_id
    assert destination.read_bytes() == payload
    assert destination.stat().st_mode & 0o077 == 0


def test_current_fetch_without_vps_transfer_identity_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        os.write(kwargs["stdout"], b"sealed archive bytes")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr("networth.backup.transport.subprocess.run", run)
    transport = SshTransport(
        host="tokyo-exit",
        user="networth",
        identity=tmp_path / "networth-backup-ssh.key",
    )
    destination = tmp_path / "download"
    with pytest.raises(TransportError, match="transfer identity"):
        transport.fetch_archive(ArchiveKind.CURRENT, destination)
    assert not destination.exists()


@pytest.mark.parametrize(
    "host,user",
    [
        ("-oProxyCommand=bad", "networth"),
        ("tokyo-exit;bad", "networth"),
        ("tokyo-exit", "-root"),
        ("tokyo-exit", "root;bad"),
    ],
)
def test_host_and_user_cannot_become_ssh_options_or_shell_syntax(
    tmp_path: Path, host: str, user: str
) -> None:
    with pytest.raises(ValueError, match="grammar"):
        SshTransport(host=host, user=user, identity=tmp_path / "key")
