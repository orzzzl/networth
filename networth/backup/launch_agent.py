"""Idempotent one-command installation of the Mac puller's KeepAlive job."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

LABEL = "com.zeleng.networth-backup-pull"
DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60


class LaunchAgentError(RuntimeError):
    """The puller plist could not be written or loaded."""


@dataclass(frozen=True, slots=True)
class LaunchAgentResult:
    label: str
    plist: Path
    executable: Path


def install_puller_launch_agent(
    *,
    ssh_host: str,
    ssh_user: str,
    ssh_key: Path,
    backup_key: Path,
    destination: Path,
    executable: Path | None = None,
    launch_agents: Path | None = None,
    logs: Path | None = None,
    uid: int | None = None,
) -> LaunchAgentResult:
    """Write, reload, and kick one label; re-running converges on that label."""

    resolved_executable = executable
    if resolved_executable is None:
        found = shutil.which("networth")
        if found is None:
            raise LaunchAgentError("networth is not on PATH; install the package before the puller")
        resolved_executable = Path(found).resolve()
    home = Path.home()
    agent_directory = launch_agents or home / "Library" / "LaunchAgents"
    log_directory = logs or home / "Library" / "Logs"
    agent_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    plist_path = agent_directory / f"{LABEL}.plist"
    arguments = [
        str(resolved_executable),
        "backup",
        "pull-loop",
        "--ssh-host",
        ssh_host,
        "--ssh-user",
        ssh_user,
        "--ssh-key",
        str(ssh_key),
        "--key-file",
        str(backup_key),
        "--destination",
        str(destination),
        "--interval",
        str(DEFAULT_INTERVAL_SECONDS),
    ]
    body = plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": arguments,
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Background",
            "StandardOutPath": str(log_directory / f"{LABEL}.log"),
            "StandardErrorPath": str(log_directory / f"{LABEL}.err.log"),
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )
    temporary = agent_directory / f".{LABEL}.{uuid.uuid4().hex}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(body)
            handle.flush()
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, plist_path)
    directory_fd = os.open(agent_directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

    selected_uid = os.getuid() if uid is None else uid
    domain = f"gui/{selected_uid}"
    service = f"{domain}/{LABEL}"
    subprocess.run(  # noqa: S603
        ["launchctl", "bootout", service],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    loaded = subprocess.run(  # noqa: S603
        ["launchctl", "bootstrap", domain, str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if loaded.returncode != 0:
        raise LaunchAgentError("launchctl bootstrap failed; the plist remains for a clean retry")
    kicked = subprocess.run(  # noqa: S603
        ["launchctl", "kickstart", service],
        capture_output=True,
        text=True,
        check=False,
    )
    if kicked.returncode != 0:
        raise LaunchAgentError("launchctl kickstart failed; re-run the same install command")
    return LaunchAgentResult(LABEL, plist_path, resolved_executable)
