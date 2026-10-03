"""Host runtime composition. Logs contain only fixed states, never provider data."""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import time
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from networth.backup.archive import BackupBuilder
from networth.backup.config import load_backup_config
from networth.backup.crypto import load_backup_key
from networth.config import SECRETS_DIR
from networth.dispatch import AlertDispatcher, DispatchResult, HealthDispatcher
from networth.filelock import LockUnavailable, exclusive_file_lock
from networth.full_cycle import FullCycleDispatcher
from networth.item_health import PollBatchResult
from networth.link_lifecycle import run_lifecycle
from networth.model import Quote
from networth.pairing import PAYLOAD_KEY_FILENAME, read_payload_key
from networth.plaid.client import PlaidClient
from networth.plaid.environment import (
    load_credentials,
    paths_for,
    selected_environment,
)
from networth.publisher import PUBLISH_INTERVAL_SECONDS, Publisher
from networth.quote_cycle import QuoteCycleDispatcher
from networth.quotes import QuoteClient, configured_feed, load_quote_credentials
from networth.storage import migrate
from networth.sync import BalanceMode
from networth.tokenstore import TokenStore

LINK_SCAN_SECONDS = 30
ARCHIVE_INTERVAL = timedelta(minutes=5)


class RuntimeQuotes:
    """No quotes credential is needed until a manual holding actually needs it."""

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        return QuoteClient(load_quote_credentials(), feed=configured_feed()).get_quotes(symbols)


def open_runtime_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    try:
        migrate(db)
    except BaseException:
        db.close()
        raise
    return db


def sync(db: sqlite3.Connection, client: PlaidClient, tokens: TokenStore, lock: Path) -> bool:
    """A failed job cannot prevent the other due jobs from being attempted."""
    jobs = (
        HealthDispatcher(db, client, tokens, lock_path=lock).run_due,
        FullCycleDispatcher(
            db, client, tokens, RuntimeQuotes(), balance_mode=BalanceMode.CACHED, lock_path=lock
        ).run_due,
        QuoteCycleDispatcher(db, RuntimeQuotes(), lock_path=lock).run_due,
    )
    ok = True
    for name, job in zip(("health", "full", "quotes"), jobs, strict=True):
        try:
            result = job()
            if isinstance(result, DispatchResult) and result.ok is False:
                ok = False
            if isinstance(result, PollBatchResult) and result.failure_types:
                ok = False
            print(f"{name}: completed", flush=True)
        except LockUnavailable:
            print(f"{name}: busy; retry next activation", flush=True)
        except Exception:
            db.rollback()
            ok = False
            print(f"{name}: failed; retry from stored state", flush=True)
    return ok


def publish(db: sqlite3.Connection, lock: Path, key_file: Path, *, at: datetime) -> str:
    # Independent of the long sync lock: this only reads committed observations
    # and performs short SQLite transactions. Alert evaluation precedes publish.
    AlertDispatcher(db, lock_path=lock).run()
    with exclusive_file_lock(lock, blocking=False):
        active = db.execute("SELECT id FROM pairing WHERE state = 'ACTIVE'").fetchall()
        if not active:
            return "waiting for phone pairing"
        snapshot = db.execute("SELECT max(id) FROM snapshot").fetchone()
        if snapshot is None or snapshot[0] is None:
            return "waiting for first snapshot"
        last = db.execute(
            "SELECT p.published_at, p.snapshot_id, p.pairing_id FROM publication p "
            "JOIN published_envelope e ON e.publication_id = p.id "
            "WHERE e.is_active = 1 ORDER BY p.seq DESC LIMIT 1"
        ).fetchone()
        if last is not None:
            stamp = datetime.fromisoformat(last[0].replace("Z", "+00:00"))
            if stamp > at:
                raise ValueError("future publication clock")
            if (
                at - stamp < timedelta(seconds=PUBLISH_INTERVAL_SECONDS)
                and last[1] == snapshot[0]
                and len(active) == 1
                and last[2] == active[0][0]
            ):
                return "not due"
        Publisher(db, lambda ref: read_payload_key(key_file, key_ref=ref)).publish(at=at)
        return "published"


def archive(*, at: datetime) -> str:
    config = load_backup_config()
    # Serialize predicate + build; BackupBuilder retains its own capture/build locks.
    with exclusive_file_lock(config.archive_dir.parent / ".archive-schedule.lock", blocking=False):
        with closing(open_runtime_database(config.database)) as db:
            row = db.execute(
                "SELECT built_at FROM backup_archive ORDER BY built_at DESC LIMIT 1"
            ).fetchone()
        if row is not None:
            stamp = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            if stamp > at:
                raise ValueError("future archive clock")
            if at - stamp < ARCHIVE_INTERVAL and (config.archive_dir / "current.nwb").is_file():
                return "not due"
        BackupBuilder(
            database=config.database,
            token_store=TokenStore(config.token_store),
            archive_dir=config.archive_dir,
            backup_key=load_backup_key(config.key_file),
        ).build_current(now=at)
        return "built"


def pending_flows(db: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in db.execute(
            "SELECT flow_id FROM link_request WHERE material_reaped_at IS NULL "
            "OR EXISTS (SELECT 1 FROM link_result r WHERE r.flow_id = link_request.flow_id "
            "AND r.state = 'EXCHANGING') ORDER BY minted_at"
        )
    ]


def link_loop(database: Path, countries: Sequence[str]) -> None:
    """Each flow owns a process; slow requests cannot queue unrelated requests.

    Never terminate an exchange to meet a scheduling deadline. A child that hangs
    inside provider/storage I/O remains visible and requires operator recovery.
    Process and conditional SQL claims prevent replay after ordinary restarts.
    """
    children: dict[str, subprocess.Popen[bytes]] = {}
    try:
        while True:
            with closing(open_runtime_database(database)) as db:
                flows = pending_flows(db)
            children = {flow: child for flow, child in children.items() if child.poll() is None}
            for flow in flows:
                if flow not in children:
                    args = [sys.executable, "-m", "networth", "daemon", "link-flow", "--flow", flow]
                    for country in countries:
                        args.extend(("--country-code", country))
                    children[flow] = subprocess.Popen(args)
            print(f"link: scan complete; active workers={len(children)}", flush=True)
            time.sleep(LINK_SCAN_SECONDS)
    finally:
        # Do not kill a worker with a returned but not yet durable credential.
        for child in children.values():
            child.wait()


def run(args: argparse.Namespace) -> int:
    try:
        environment = selected_environment()
        credentials = load_credentials(environment)  # mismatch refuses every job before mutation
        paths = paths_for(environment)
        if args.job == "link":
            link_loop(paths.database, args.country_code or ("US",))
            return 0
        with closing(open_runtime_database(paths.database)) as db:
            tokens = TokenStore(paths.items)
            if args.job == "init":
                print("runtime: configuration validated; database ready")
                return 0
            if args.job == "sync":
                return (
                    0
                    if sync(
                        db,
                        PlaidClient(credentials),
                        tokens,
                        paths.database.with_suffix(".sync.lock"),
                    )
                    else 1
                )
            if args.job == "publish":
                status = publish(
                    db,
                    paths.database.with_suffix(".publish.lock"),
                    SECRETS_DIR / PAYLOAD_KEY_FILENAME,
                    at=datetime.now(UTC),
                )
            elif args.job == "archive":
                config = load_backup_config()
                if config.database != paths.database or config.token_store != paths.items:
                    raise ValueError("archive environment differs from daemon")
                status = archive(at=datetime.now(UTC))
            else:
                if not args.flow:
                    raise ValueError("Link completion requires an existing flow")
                outcome = run_lifecycle(
                    db,
                    tokens,
                    PlaidClient(credentials),
                    flow_id=args.flow,
                    country_codes=args.country_code or ("US",),
                )
                print("link: pass completed")
                return 1 if outcome.worker.failed or outcome.worker.held else 0
            print(f"{args.job}: {status}")
        return 0
    except LockUnavailable:
        print("runtime: busy; retry next activation")
        return 0
    except Exception:
        # Never print exception text/tracebacks, which may contain response data.
        print("runtime: failed; inspect configuration and stored diagnostics", file=sys.stderr)
        return 1
