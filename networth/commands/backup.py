"""Build, pull, verify, restore, and report encrypted backups (task 03a)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from networth.backup.archive import BackupBuilder
from networth.backup.config import BackupConfig, load_backup_config
from networth.backup.crypto import load_backup_key
from networth.backup.dispatcher import BackupDispatcher
from networth.backup.drill import run_restore_drill
from networth.backup.launch_agent import install_puller_launch_agent
from networth.backup.puller import BackupPuller, run_canary
from networth.backup.restore import restore_archive
from networth.backup.state import BackupStateStore, open_database
from networth.backup.transport import SshTransport
from networth.tokenstore import TokenStore

SUMMARY = "Build, pull, verify, and restore encrypted backups."

_MAC_SECRETS = Path.home() / "agents" / "secrets"
_MAC_ARCHIVES = _MAC_SECRETS / "networth-backups"
_MAC_BACKUP_KEY = _MAC_SECRETS / "networth-backup.key"
_MAC_SSH_KEY = _MAC_SECRETS / "networth-backup-ssh.key"


def _add_vps_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", type=Path)
    parser.add_argument("--token-store", type=Path)
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--key-file", type=Path)


def _add_mac_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ssh-host", default="tokyo-exit")
    parser.add_argument("--ssh-user", default="networth")
    parser.add_argument("--ssh-key", type=Path, default=_MAC_SSH_KEY)
    parser.add_argument("--key-file", type=Path, default=_MAC_BACKUP_KEY)
    parser.add_argument("--destination", type=Path, default=_MAC_ARCHIVES)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="backup_action", metavar="<action>", required=True)

    build = actions.add_parser(
        "build-archive", help="build and atomically publish the real archive"
    )
    build.add_argument("kind", choices=("current",))
    _add_vps_paths(build)
    build.set_defaults(_backup_handler=_run_build_archive)

    probe = actions.add_parser("build-probe", help="build the rate-limited fixed-path canary")
    _add_vps_paths(probe)
    probe.set_defaults(_backup_handler=_run_build_probe)

    serve = actions.add_parser("serve-archive", help="stream current or probe archive")
    serve.add_argument("kind", choices=("current", "probe"))
    _add_vps_paths(serve)
    serve.set_defaults(_backup_handler=_run_serve)

    record_pull = actions.add_parser("record-pull", help="record a destination verification")
    record_pull.add_argument("archive_id")
    record_pull.add_argument("verdict", choices=("VERIFIED", "FAILED"))
    record_pull.add_argument("pulled_by")
    _add_vps_paths(record_pull)
    record_pull.set_defaults(_backup_handler=_run_record_pull)

    record_drill = actions.add_parser("record-drill", help="record an offline restore verdict")
    record_drill.add_argument("archive_id")
    record_drill.add_argument("verdict", choices=("VERIFIED", "FAILED"))
    _add_vps_paths(record_drill)
    record_drill.set_defaults(_backup_handler=_run_record_drill)

    attest = actions.add_parser("attest-key", help="record the owner's escrow attestation")
    _add_vps_paths(attest)
    attest.set_defaults(_backup_handler=_run_attest_key)

    doctor = actions.add_parser("doctor", help="show backup age and refusal counters")
    _add_vps_paths(doctor)
    doctor.set_defaults(_backup_handler=_run_doctor)

    dispatch = actions.add_parser("ssh-dispatch", help=argparse.SUPPRESS)
    _add_vps_paths(dispatch)
    dispatch.set_defaults(_backup_handler=_run_dispatch)

    pull = actions.add_parser("pull", help="pull, fsync, verify, rename, and write back")
    _add_mac_paths(pull)
    pull.set_defaults(_backup_handler=_run_pull)

    loop = actions.add_parser("pull-loop", help="long-running KeepAlive pull process")
    _add_mac_paths(loop)
    loop.add_argument("--interval", type=int, default=6 * 60 * 60)
    loop.set_defaults(_backup_handler=_run_pull_loop)

    canary = actions.add_parser("canary", help="prove the restricted backup path before Link")
    _add_mac_paths(canary)
    canary.set_defaults(_backup_handler=_run_canary)

    drill = actions.add_parser("restore-drill", help="run the self-contained offline drill")
    _add_mac_paths(drill)
    drill.add_argument("--archive", type=Path)
    drill.set_defaults(_backup_handler=_run_restore_drill)

    restore = actions.add_parser("restore", help="restore into a new empty directory")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--key-file", type=Path, default=_MAC_BACKUP_KEY)
    restore.add_argument("--destination", type=Path, required=True)
    restore.add_argument("--reason", default="disaster recovery")
    restore.set_defaults(_backup_handler=_run_restore)

    install = actions.add_parser("install-puller", help="install/reload the KeepAlive LaunchAgent")
    _add_mac_paths(install)
    install.add_argument("--executable", type=Path)
    install.set_defaults(_backup_handler=_run_install)


def _vps_config(args: argparse.Namespace) -> BackupConfig:
    database = getattr(args, "database", None)
    token_store = getattr(args, "token_store", None)
    archive_dir = getattr(args, "archive_dir", None)
    key_file = getattr(args, "key_file", None)
    values = (database, token_store, archive_dir, key_file)
    if any(value is not None for value in values) and not all(
        isinstance(value, Path) for value in values
    ):
        raise ValueError("provide all four VPS paths or none of them")
    if all(isinstance(value, Path) for value in values):
        assert isinstance(database, Path)
        assert isinstance(token_store, Path)
        assert isinstance(archive_dir, Path)
        assert isinstance(key_file, Path)
        return BackupConfig(
            database=database,
            token_store=token_store,
            archive_dir=archive_dir,
            key_file=key_file,
        )
    return load_backup_config()


def _builder(config: BackupConfig) -> BackupBuilder:
    return BackupBuilder(
        database=config.database,
        token_store=TokenStore(config.token_store),
        archive_dir=config.archive_dir,
        backup_key=load_backup_key(config.key_file),
    )


def _dispatcher(args: argparse.Namespace) -> BackupDispatcher:
    config = _vps_config(args)
    return BackupDispatcher(builder=_builder(config), database=config.database)


def _run_build_archive(args: argparse.Namespace) -> int:
    result = _builder(_vps_config(args)).build_current()
    print(
        json.dumps(
            {
                "archive_id": result.archive_id,
                "archive_sha256": result.archive_sha256,
                "byte_size": result.byte_size,
                "path": str(result.path),
            },
            sort_keys=True,
        )
    )
    return 0


def _run_build_probe(args: argparse.Namespace) -> int:
    result = _builder(_vps_config(args)).build_probe()
    print(
        json.dumps(
            {"outcome": result.outcome.value, "probe_generation": result.probe_generation},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    return _dispatcher(args).dispatch(f"serve-archive {args.kind}")


def _run_record_pull(args: argparse.Namespace) -> int:
    return _dispatcher(args).dispatch(
        f"record-pull {args.archive_id} {args.verdict} {args.pulled_by}"
    )


def _run_record_drill(args: argparse.Namespace) -> int:
    return _dispatcher(args).dispatch(f"record-drill {args.archive_id} {args.verdict}")


def _run_attest_key(args: argparse.Namespace) -> int:
    config = _vps_config(args)
    with closing(open_database(config.database)) as connection:
        BackupStateStore(connection).attest_key(at=datetime.now(UTC))
        connection.commit()
    print("backup key escrow attestation recorded (owner claim, not verified proof)")
    return 0


def _format_datetime(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _run_doctor(args: argparse.Namespace) -> int:
    config = _vps_config(args)
    with closing(open_database(config.database)) as connection:
        status = BackupStateStore(connection).status()
    print(
        json.dumps(
            {
                "dispatch_rejection_count": status.dispatch_rejection_count,
                "key_escrow_confirmed_at": _format_datetime(status.key_escrow_confirmed_at),
                "key_escrow_is_owner_attestation": True,
                "last_successful_backup": _format_datetime(status.last_successful_backup),
                "last_verified_restore_archive_id": status.last_verified_restore_archive_id,
                "last_verified_restore_at": _format_datetime(status.last_verified_restore_at),
                "last_verified_restore_error": status.last_verified_restore_error,
                "probe_generation": status.probe_generation,
                "probe_refusal_count": status.probe_refusal_count,
            },
            sort_keys=True,
        )
    )
    return 0


def _run_dispatch(args: argparse.Namespace) -> int:
    return _dispatcher(args).dispatch(os.environ.get("SSH_ORIGINAL_COMMAND"))


def _transport(args: argparse.Namespace) -> SshTransport:
    return SshTransport(host=args.ssh_host, user=args.ssh_user, identity=args.ssh_key)


def _puller(args: argparse.Namespace) -> BackupPuller:
    return BackupPuller(
        transport=_transport(args),
        destination=args.destination,
        backup_key=load_backup_key(args.key_file),
    )


def _run_pull(args: argparse.Namespace) -> int:
    result = _puller(args).run_once()
    print(
        json.dumps(
            {
                "archive_id": result.archive_id,
                "power_source": result.power_source.value,
                "report_recorded": result.report_recorded,
                "transferred": result.transferred,
                "verified": True,
            },
            sort_keys=True,
        )
    )
    return 0


def _run_pull_loop(args: argparse.Namespace) -> int:
    if args.interval <= 0:
        raise ValueError("pull interval must be positive")
    puller = _puller(args)
    while True:
        try:
            puller.run_once()
        except Exception as exc:
            print(f"networth backup pull failed: {type(exc).__name__}", file=sys.stderr)
        time.sleep(args.interval)


def _run_canary(args: argparse.Namespace) -> int:
    result = run_canary(
        transport=_transport(args),
        destination=args.destination,
        backup_key=load_backup_key(args.key_file),
        local_observed_at=datetime.now(UTC),
    )
    print(json.dumps({"probe_generation": result.probe_generation, "verified": True}))
    return 0


def _run_restore_drill(args: argparse.Namespace) -> int:
    archive = args.archive or args.destination / "current.nwb"
    result = run_restore_drill(
        archive=archive,
        backup_key=load_backup_key(args.key_file),
        local_state_directory=args.destination,
        transport=_transport(args),
    )
    print(
        json.dumps(
            {
                "archive_id": result.archive_id,
                "orphan_token_count": result.orphan_token_count,
                "report_recorded": result.report_recorded,
                "verified": result.verified,
            },
            sort_keys=True,
        )
    )
    return 0


def _run_restore(args: argparse.Namespace) -> int:
    result = restore_archive(
        args.archive,
        load_backup_key(args.key_file),
        args.destination,
        reason=args.reason,
    )
    print(
        json.dumps(
            {
                "archive_id": result.archive_id,
                "destination": str(result.destination),
                "orphan_token_count": result.orphan_token_count,
                "publish_epoch": result.publish_epoch,
                "replay_drill_passed": result.replay.passed,
            },
            sort_keys=True,
        )
    )
    return 0


def _run_install(args: argparse.Namespace) -> int:
    result = install_puller_launch_agent(
        ssh_host=args.ssh_host,
        ssh_user=args.ssh_user,
        ssh_key=args.ssh_key,
        backup_key=args.key_file,
        destination=args.destination,
        executable=args.executable,
    )
    print(
        f"installed and loaded {result.label} from {result.plist}; "
        f"KeepAlive puller uses {result.executable}"
    )
    return 0


def run(args: argparse.Namespace) -> int:
    handler = args._backup_handler
    return int(handler(args))
