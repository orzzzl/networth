"""Integrated synthetic lifecycle: real SQLite and credential durability boundaries."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from networth.item_budget import ItemBudgetError, read_item_budget
from networth.link_lifecycle import (
    LifecycleError,
    adjudicate_closure,
    authorize_release,
    mint_request,
    request_abandon,
    run_lifecycle,
)
from networth.link_observations import adjudicate_observation
from networth.link_recovery import MintResult, RecoveryRecord, store_and_verify
from networth.mac_identity import REQUIRED_HOLDER
from networth.plaid.client import (
    ExchangedItem,
    HostedLinkToken,
    ItemInstitution,
    LinkSessionPoll,
    LinkSessionRecord,
    LinkSessionShape,
)
from networth.storage import migrate
from networth.tokenstore import Secret, SecretKind, TokenStore, UnknownSecretRef

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
LINK = "synthetic-link-lifecycle"
URL = "https://synthetic.invalid/link-lifecycle"
AUDIT = "a" * 32


class Client:
    def __init__(self) -> None:
        self.mints = 0
        self.polls = 0
        self.exchanges = 0
        self.poll = LinkSessionPoll(LinkSessionShape.SESSIONS_ABSENT, ())
        self.error = False

    def link_token_create_hosted(self, **kwargs: Any) -> HostedLinkToken:
        self.mints += 1
        assert kwargs["url_lifetime_seconds"] == 1800
        return HostedLinkToken(LINK, URL, NOW + timedelta(minutes=30), 1800)

    def link_token_get(self, token: str) -> LinkSessionPoll:
        self.polls += 1
        assert token == LINK
        if self.error:
            raise RuntimeError(LINK + URL)
        return self.poll

    def item_public_token_exchange(self, token: str) -> ExchangedItem:
        self.exchanges += 1
        return ExchangedItem(
            "synthetic-access-lifecycle", "synthetic-item-lifecycle", "syntheticrequest"
        )

    def item_institution(
        self, token: str, *, expected_item_id: str, country_codes: Sequence[str]
    ) -> ItemInstitution:
        return ItemInstitution(
            expected_item_id, "synthetic-institution-lifecycle", "Synthetic", False
        )


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[sqlite3.Connection, TokenStore, Client, MintResult]]:
    db = sqlite3.connect(tmp_path / "db.sqlite")
    migrate(db)
    store = TokenStore(tmp_path / "tokens")
    client = Client()
    minted = mint_request(db, store, client, country_codes=("US",), clock=lambda: NOW)
    yield db, store, client, minted
    db.close()


def run(setup: Any, now: datetime = NOW) -> Any:
    db, store, client, minted = setup
    return run_lifecycle(
        db, store, client, flow_id=minted.flow_id, country_codes=("US",), clock=lambda: now
    )


def release(setup: Any, tmp_path: Path) -> RecoveryRecord:
    db, store, _, mint = setup
    record = store_and_verify(
        tmp_path / "recovery",
        mint.as_record(now=NOW, automatic=True),
        holder=REQUIRED_HOLDER,
        now=NOW,
    )
    authorize_release(db, store, record, clock=lambda: NOW)
    return record


def resolve(db: sqlite3.Connection, additional: int = 0) -> None:
    observation = db.execute(
        "SELECT observation_id FROM link_success_observation WHERE reason='COVERAGE_UNPROVEN'"
    ).fetchone()[0]
    adjudicate_observation(
        db,
        observation_id=observation,
        additional_slots=additional,
        audit_id=AUDIT,
        now=NOW + timedelta(hours=8),
        reviewed=True,
    )


def test_mint_durable_request_and_immediate_poll_before_release(setup: Any) -> None:
    db, store, client, mint = setup
    assert client.mints == 1 and client.polls == 1
    assert store.get("link-token." + mint.flow_id).reveal() == LINK
    assert db.execute(
        "SELECT lifecycle_protocol, url_release_authorized_at FROM link_request"
    ).fetchone() == ("networth.automatic-link.1", None)
    assert db.execute("SELECT outcome FROM link_poll_history").fetchall() == [("OBSERVED",)]
    dump = "\n".join(db.iterdump())
    assert LINK not in dump and URL not in dump


def test_mint_poll_failure_keeps_request_without_remint(tmp_path: Path) -> None:
    db = sqlite3.connect(tmp_path / "db")
    migrate(db)
    store = TokenStore(tmp_path / "tokens")
    client = Client()
    client.error = True
    result = mint_request(db, store, client, country_codes=("US",), clock=lambda: NOW)
    assert client.mints == 1
    assert db.execute("SELECT poll_error FROM link_request").fetchone() == ("POLL_FAILED",)
    assert store.get("link-token." + result.flow_id).reveal() == LINK
    client.error = False
    run((db, store, client, result))
    assert client.mints == 1 and client.polls == 2
    db.close()


def test_release_ack_is_idempotent_and_monotonic(setup: Any, tmp_path: Path) -> None:
    db, store, _, _ = setup
    record = release(setup, tmp_path)
    before = db.execute(
        "SELECT second_copy_verified_at, url_release_authorized_at FROM link_request"
    ).fetchone()
    authorize_release(db, store, record, clock=lambda: NOW + timedelta(minutes=1))
    assert (
        db.execute(
            "SELECT second_copy_verified_at, url_release_authorized_at FROM link_request"
        ).fetchone()
        == before
    )


@pytest.mark.parametrize(
    "bad", ["token", "holder", "protocol", "expired", "closed", "abandoned", "migrated", "reaped"]
)
def test_release_refuses_mismatched_or_terminal_request(setup: Any, bad: str) -> None:
    db, store, _, mint = setup
    record = replace(
        mint.as_record(now=NOW, automatic=True),
        second_copy_holder=REQUIRED_HOLDER,
        second_copy_verified_at=NOW,
    )
    now = NOW
    if bad == "token":
        record = replace(record, link_token=Secret("synthetic-wrong"))
    if bad == "holder":
        record = replace(record, second_copy_holder="synthetic-wrong-holder")
    if bad == "protocol":
        record = mint.as_record(now=NOW)
    if bad == "expired":
        now += timedelta(minutes=30)
    for kind, column in [
        ("closed", "polling_closed_at"),
        ("abandoned", "abandon_requested_at"),
        ("reaped", "material_reaped_at"),
    ]:
        if bad == kind:
            db.execute(f"UPDATE link_request SET {column} = ?", (NOW.isoformat(),))
    if bad == "migrated":
        db.execute("UPDATE link_request SET lifecycle_protocol = NULL")
    db.commit()
    with pytest.raises(LifecycleError):
        authorize_release(db, store, record, clock=lambda: now)
    assert db.execute("SELECT url_release_authorized_at FROM link_request").fetchone() == (None,)


@pytest.mark.parametrize("abandon", [False, True])
def test_never_released_request_has_local_no_success_proof(setup: Any, abandon: bool) -> None:
    db, store, _, mint = setup
    if abandon:
        request_abandon(db, flow_id=mint.flow_id, now=NOW)
    outcome = run(setup, NOW if abandon else NOW + timedelta(hours=8))
    assert outcome.closed and outcome.reaped
    assert db.execute("SELECT state, secret_ref FROM link_request").fetchone() == (
        "ABANDONED" if abandon else "URL_EXPIRED",
        None,
    )
    assert read_item_budget(db).remaining == 10
    with pytest.raises(UnknownSecretRef):
        store.get("link-token." + mint.flow_id)


@pytest.mark.parametrize("migrated", [False, True])
def test_retention_gap_creates_durable_hold_not_zero_cost_closure(
    setup: Any, tmp_path: Path, migrated: bool
) -> None:
    db, store, _, mint = setup
    if migrated:
        db.execute("UPDATE link_request SET lifecycle_protocol = NULL")
        db.commit()
    else:
        release(setup, tmp_path)
    late = NOW + timedelta(hours=8)
    outcome = run(setup, late)
    assert not outcome.closed and not outcome.reaped and outcome.worker.held
    with pytest.raises(ItemBudgetError):
        read_item_budget(db)
    assert db.execute(
        "SELECT reason, max_reported_results FROM link_success_observation"
    ).fetchone() == ("COVERAGE_UNPROVEN", None)
    assert db.execute("SELECT COUNT(*) FROM link_result").fetchone() == (0,)
    assert store.get("link-token." + mint.flow_id).reveal() == LINK
    run(setup, late + timedelta(minutes=1))
    with pytest.raises(ItemBudgetError):
        read_item_budget(db)
    assert db.execute("SELECT COUNT(*) FROM link_success_observation").fetchone() == (1,)
    resolve(db)
    run(setup, late + timedelta(minutes=2))
    assert read_item_budget(db).remaining == 10
    assert db.execute("SELECT polling_closed_at FROM link_request").fetchone() == (None,)
    assert adjudicate_closure(
        db, store, flow_id=mint.flow_id, audit_id=AUDIT, now=late, reviewed=True
    ) == (True, True)


def test_frequent_empty_polls_do_not_manufacture_completeness(setup: Any, tmp_path: Path) -> None:
    release(setup, tmp_path)
    for minute in (5, 10, 15, 20, 25, 30):
        outcome = run(setup, NOW + timedelta(minutes=minute))
    assert outcome.worker.held and not outcome.closed


def test_abandon_and_child_exit_do_not_close_reopenable_url(setup: Any, tmp_path: Path) -> None:
    db, store, client, mint = setup
    release(setup, tmp_path)
    request_abandon(db, flow_id=mint.flow_id, now=NOW)
    client.poll = LinkSessionPoll(
        LinkSessionShape.SESSION_IN_PROGRESS,
        (LinkSessionRecord("synthetic-exit", NOW, NOW, (), 0),),
    )
    outcome = run(setup)
    assert not outcome.closed and not outcome.reaped
    assert db.execute("SELECT state FROM link_session").fetchone() == ("SESSION_EXITED",)
    assert store.get("link-token." + mint.flow_id).reveal() == LINK
    assert read_item_budget(db).remaining == 10


def test_multi_session_success_preserves_access_and_request_until_review(
    setup: Any, tmp_path: Path
) -> None:
    db, store, client, mint = setup
    release(setup, tmp_path)
    client.poll = LinkSessionPoll(
        LinkSessionShape.ITEM_ADDED,
        (LinkSessionRecord("synthetic-success", NOW, NOW, ("synthetic-public-lifecycle",), 1),),
    )
    assert run(setup).worker.finalized == 1
    access_ref = db.execute("SELECT secret_ref FROM link_result").fetchone()[0]
    assert not run(setup).closed
    client.poll = LinkSessionPoll(LinkSessionShape.SESSIONS_ABSENT, ())
    late = NOW + timedelta(hours=8)
    run(setup, late)
    resolve(db)
    assert adjudicate_closure(
        db, store, flow_id=mint.flow_id, audit_id=AUDIT, now=late, reviewed=True
    ) == (True, True)
    assert db.execute("SELECT state FROM link_request").fetchone() == ("URL_MINTED",)
    assert store.get(access_ref).reveal() == "synthetic-access-lifecycle"
    assert read_item_budget(db).remaining == 9
    assert client.exchanges == 1


@pytest.mark.parametrize(
    "bad", ["unfinished", "pending", "poll_failure", "material_hold", "unknown_deadline"]
)
def test_closure_refuses_unresolved_evidence(setup: Any, tmp_path: Path, bad: str) -> None:
    db, store, client, mint = setup
    release(setup, tmp_path)
    late = NOW + timedelta(hours=8)
    run(setup, late)
    resolve(db)
    if bad == "unfinished":
        db.execute(
            "INSERT INTO link_session VALUES (?, 'synthetic-unfinished', 'SESSION_STARTED', "
            "NULL, NULL)",
            (mint.flow_id,),
        )
    elif bad in ("pending", "unknown_deadline"):
        db.execute(
            "INSERT INTO link_result(result_id, flow_id, token_digest, state) VALUES (?, ?, "
            "'synthetic-digest', ?)",
            (
                "b" * 32,
                mint.flow_id,
                "SUCCESS_PENDING_EXCHANGE" if bad == "pending" else "EXCHANGE_UNCERTAIN",
            ),
        )
    elif bad == "material_hold":
        db.execute(
            "INSERT INTO link_material_hold(hold_id, flow_id, reason, observed_at) VALUES (?, "
            "?, 'UNVERIFIED_MATERIAL', ?)",
            ("b" * 32, mint.flow_id, late.isoformat()),
        )
    else:
        client.error = True
        run(setup, late)
    db.commit()
    if bad == "material_hold":
        assert adjudicate_closure(
            db, store, flow_id=mint.flow_id, audit_id=AUDIT, now=late, reviewed=True
        ) == (False, False)
    else:
        with pytest.raises(LifecycleError):
            adjudicate_closure(
                db, store, flow_id=mint.flow_id, audit_id=AUDIT, now=late, reviewed=True
            )
    assert store.get("link-token." + mint.flow_id).reveal() == LINK


@pytest.mark.parametrize("partial", ["both", "no_material", "no_ref", "delete_crash"])
def test_reaper_repairs_three_partial_states_and_delete_crash(
    setup: Any, monkeypatch: pytest.MonkeyPatch, partial: str
) -> None:
    db, store, _, mint = setup
    ref = "link-token." + mint.flow_id
    if partial == "no_material":
        store.delete(ref)
    if partial == "no_ref":
        db.execute("UPDATE link_request SET secret_ref=NULL")
        db.commit()
    if partial == "delete_crash":
        original = store.delete

        def crash(ref: str) -> bool:
            original(ref)
            raise OSError("synthetic crash")

        monkeypatch.setattr(store, "delete", crash)
        with pytest.raises(LifecycleError):
            run(setup, NOW + timedelta(hours=8))
        assert db.execute("SELECT secret_ref FROM link_request").fetchone() == (ref,)
        monkeypatch.setattr(store, "delete", original)
    assert run(setup, NOW + timedelta(hours=8)).reaped
    assert run(setup, NOW + timedelta(hours=9)).reaped
    assert db.execute("SELECT secret_ref FROM link_request").fetchone() == (None,)


def test_stranded_children_keep_material_to_latest_diagnostics_deadline(
    setup: Any, tmp_path: Path
) -> None:
    db, store, client, mint = setup
    release(setup, tmp_path)
    late = NOW + timedelta(hours=1)
    run(setup, late)
    resolve(db)
    for index, hours in enumerate((6, 7)):
        db.execute(
            "INSERT INTO link_result(result_id, flow_id, token_digest, state, finished_at, "
            "session_retention_expires_at) VALUES (?, ?, ?, 'TOKEN_EXPIRED', ?, ?)",
            (
                str(index) * 32,
                mint.flow_id,
                str(index),
                NOW.isoformat(),
                (NOW + timedelta(hours=hours)).isoformat(),
            ),
        )
    db.commit()
    assert adjudicate_closure(
        db, store, flow_id=mint.flow_id, audit_id=AUDIT, now=late, reviewed=True
    ) == (True, False)
    assert not run(setup, NOW + timedelta(hours=6)).reaped
    assert run(setup, NOW + timedelta(hours=7)).reaped
    assert read_item_budget(db).remaining == 8


def test_reaper_holds_worker_file_lock_and_sql_write_lock(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    import sys

    from networth.link_worker import request_lock_path

    db, store, _, mint = setup
    original = store.delete
    visited = False

    def inspect(ref: str) -> bool:
        nonlocal visited
        visited = True
        # The same process can reacquire flock; a separate process is required.
        code = (
            "import fcntl,sys; f=open(sys.argv[1], 'a'); "
            "fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB)"
        )
        contender = subprocess.run(
            [sys.executable, "-c", code, str(request_lock_path(db, mint.flow_id))],
            capture_output=True,
            text=True,
        )
        assert contender.returncode != 0 and "BlockingIOError" in contender.stderr
        name = next(r[2] for r in db.execute("PRAGMA database_list") if r[1] == "main")
        other = sqlite3.connect(name, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        finally:
            other.close()
        assert db.execute("SELECT secret_ref FROM link_request").fetchone()[0] == ref
        return bool(original(ref))

    monkeypatch.setattr(store, "delete", inspect)
    assert run(setup, NOW + timedelta(hours=8)).reaped
    assert visited


def test_reaper_never_deletes_arbitrary_access_reference(setup: Any) -> None:
    db, store, _, mint = setup
    ref = store.put(
        SecretKind.ACCESS_TOKEN, "c" * 32, "synthetic-other-access", item_id="synthetic-other-item"
    )
    db.execute("UPDATE link_request SET secret_ref=?", (ref,))
    db.commit()
    outcome = run(setup, NOW + timedelta(hours=8))
    assert outcome.worker.held and not outcome.reaped
    assert store.get(ref).reveal() == "synthetic-other-access"
    assert store.get("link-token." + mint.flow_id).reveal() == LINK


def test_unknown_diagnostics_deadline_prevents_deletion_after_closure(
    setup: Any, tmp_path: Path
) -> None:
    db, store, _, mint = setup
    release(setup, tmp_path)
    late = NOW + timedelta(hours=8)
    run(setup, late)
    resolve(db)
    db.execute(
        "INSERT INTO link_result(result_id, flow_id, token_digest, state, finished_at) "
        "VALUES (?, ?, 'synthetic-unknown-deadline', 'EXCHANGE_UNCERTAIN', ?)",
        ("b" * 32, mint.flow_id, NOW.isoformat()),
    )
    db.commit()
    assert adjudicate_closure(
        db, store, flow_id=mint.flow_id, audit_id=AUDIT, now=late, reviewed=True
    ) == (True, False)
    assert not run(setup, late + timedelta(days=1)).reaped
    assert store.get("link-token." + mint.flow_id).reveal() == LINK


def test_unexpired_request_refuses_operator_closure(setup: Any, tmp_path: Path) -> None:
    db, store, _, mint = setup
    release(setup, tmp_path)
    assert adjudicate_closure(
        db, store, flow_id=mint.flow_id, audit_id=AUDIT, now=NOW, reviewed=True
    ) == (False, False)


def test_budget_hold_blocks_new_mint_before_provider_call(setup: Any, tmp_path: Path) -> None:
    db, store, client, _ = setup
    release(setup, tmp_path)
    run(setup, NOW + timedelta(hours=8))
    with pytest.raises(ItemBudgetError):
        mint_request(db, store, client, country_codes=("US",), clock=lambda: NOW)
    assert client.mints == 1


def test_coverage_slots_survive_reopen_and_nonzero_closure_keeps_success_parent(
    setup: Any, tmp_path: Path
) -> None:
    db, store, _, mint = setup
    release(setup, tmp_path)
    late = NOW + timedelta(hours=8)
    run(setup, late)
    resolve(db, additional=2)
    assert read_item_budget(db).remaining == 8
    assert adjudicate_closure(
        db, store, flow_id=mint.flow_id, audit_id=AUDIT, now=late, reviewed=True
    ) == (True, True)
    assert db.execute("SELECT state FROM link_request").fetchone() == ("URL_MINTED",)
    assert db.execute("SELECT additional_slots FROM link_observation_adjudication").fetchone() == (
        2,
    )


def test_release_reads_server_clock_after_acquiring_request_lock(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import contextmanager

    from networth import link_lifecycle
    from networth.filelock import exclusive_file_lock

    db, store, _, mint = setup
    record = replace(
        mint.as_record(now=NOW, automatic=True),
        second_copy_holder=REQUIRED_HOLDER,
        second_copy_verified_at=NOW,
    )
    instant = NOW

    @contextmanager
    def delayed_lock(path: Path) -> Iterator[None]:
        nonlocal instant
        with exclusive_file_lock(path):
            instant = NOW + timedelta(minutes=31)
            yield

    monkeypatch.setattr(link_lifecycle, "exclusive_file_lock", delayed_lock)
    with pytest.raises(LifecycleError):
        authorize_release(db, store, record, clock=lambda: instant)
    assert db.execute("SELECT url_release_authorized_at FROM link_request").fetchone() == (None,)
