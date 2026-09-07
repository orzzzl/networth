"""The puller installs as one convergent KeepAlive LaunchAgent."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from networth.backup.launch_agent import LABEL, LaunchAgentError, install_puller_launch_agent


def _arguments(tmp_path: Path) -> dict[str, Any]:
    return {
        "ssh_host": "tokyo-exit",
        "ssh_user": "networth",
        "ssh_key": tmp_path / "networth-backup-ssh.key",
        "backup_key": tmp_path / "networth-backup.key",
        "destination": tmp_path / "archives",
        "executable": tmp_path / "bin" / "networth",
        "launch_agents": tmp_path / "LaunchAgents",
        "logs": tmp_path / "Logs",
        "uid": 501,
    }


def test_installer_writes_keepalive_never_interval_and_reloads_one_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("networth.backup.launch_agent.subprocess.run", run)
    arguments = _arguments(tmp_path)
    first = install_puller_launch_agent(**arguments)
    first_bytes = first.plist.read_bytes()
    second = install_puller_launch_agent(**arguments)

    assert first == second
    assert second.plist.read_bytes() == first_bytes
    plist = plistlib.loads(first_bytes)
    assert plist["Label"] == LABEL
    assert plist["KeepAlive"] is True
    assert plist["RunAtLoad"] is True
    assert "StartInterval" not in plist
    assert plist["ProgramArguments"][1:3] == ["backup", "pull-loop"]
    assert "zelengs-macbook-air-2" not in " ".join(plist["ProgramArguments"])
    assert (
        calls
        == [
            ["launchctl", "bootout", f"gui/501/{LABEL}"],
            ["launchctl", "bootstrap", "gui/501", str(first.plist)],
            ["launchctl", "kickstart", f"gui/501/{LABEL}"],
        ]
        * 2
    )


def test_failed_load_leaves_one_plist_that_the_same_command_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_attempts = 0

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal bootstrap_attempts
        if argv[1] == "bootstrap":
            bootstrap_attempts += 1
            return subprocess.CompletedProcess(argv, 1 if bootstrap_attempts == 1 else 0, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("networth.backup.launch_agent.subprocess.run", run)
    arguments = _arguments(tmp_path)
    with pytest.raises(LaunchAgentError, match="bootstrap"):
        install_puller_launch_agent(**arguments)
    plist = tmp_path / "LaunchAgents" / f"{LABEL}.plist"
    assert plist.is_file()

    result = install_puller_launch_agent(**arguments)
    assert result.plist == plist
    assert list(plist.parent.glob(f"{LABEL}.plist")) == [plist]
