"""Synthetic fault injection around the real SQLite, flock and TokenStore boundaries."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from networth.cli import main
from networth.item_budget import read_item_budget
from networth.link_observations import ingest_poll
from networth.link_worker import WorkerError, WorkerOutcome, request_lock_path, run_request
from networth.plaid.client import (
    ExchangedItem,
    ItemInstitution,
    LinkSessionPoll,
    LinkSessionRecord,
    LinkSessionShape,
    PlaidCallError,
)
from networth.plaid.environment import Paths
from networth.storage import migrate
from networth.tokenstore import SecretKind, TokenStore, UnverifiedMaterial

FLOW = "1" * 32
NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
STAMP = "2026-09-23T12:00:00Z"
LINK = "synthetic-link-sentinel"
PUBLIC = "synthetic-public-sentinel"
ACCESS = "synthetic-access-sentinel"
ITEM = "synthetic-item-sentinel"
REQUEST = "syntheticrequest"


class Crash(BaseException):
    pass


def session(
    sid: str = "synthetic-session", token: str = PUBLIC, finished: datetime | None = NOW
) -> LinkSessionRecord:
    return LinkSessionRecord(sid, NOW - timedelta(minutes=1), finished, (token,), 1)


class Client:
    def __init__(self) -> None:
        self.poll = LinkSessionPoll(LinkSessionShape.ITEM_ADDED, (session(),))
        self.poll_error: Exception | None = None
        self.exchange_error: BaseException | None = None
        self.metadata_error: Exception | None = None
        self.polls = 0
        self.exchanges: list[str] = []
        self.metadata_calls = 0
        self.same_item = False
        self.response: ExchangedItem | None = None

    def link_token_get(self, link_token: str) -> LinkSessionPoll:
        assert link_token == LINK
        self.polls += 1
        if self.poll_error:
            raise self.poll_error
        return self.poll

    def item_public_token_exchange(self, public_token: str) -> ExchangedItem:
        self.exchanges.append(public_token)
        if self.exchange_error:
            raise self.exchange_error
        if self.response:
            return self.response
        suffix = "" if public_token == PUBLIC else "-second"
        return ExchangedItem(ACCESS + suffix, ITEM if self.same_item else ITEM + suffix, REQUEST)

    def item_institution(
        self, access_token: str, *, expected_item_id: str, country_codes: Sequence[str]
    ) -> ItemInstitution:
        assert country_codes == ("US",)
        self.metadata_calls += 1
        if self.metadata_error:
            raise self.metadata_error
        return ItemInstitution(expected_item_id, "synthetic-institution", "Synthetic Name", False)


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[sqlite3.Connection, TokenStore, Client]]:
    db = sqlite3.connect(tmp_path / "synthetic.sqlite")
    migrate(db)
    store = TokenStore(tmp_path / "tokens")
    ref = store.put(SecretKind.LINK_TOKEN, FLOW, LINK)
    db.execute(
        "INSERT INTO link_request(flow_id, secret_ref, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, ?, 'URL_MINTED')",
        (FLOW, ref, STAMP, STAMP),
    )
    db.commit()
    yield db, store, Client()
    db.close()


def run(setup: tuple[sqlite3.Connection, TokenStore, Client], now: datetime = NOW) -> WorkerOutcome:
    db, store, client = setup
    return run_request(db, store, client, flow_id=FLOW, country_codes=("US",), clock=lambda: now)


def state(db: sqlite3.Connection) -> str:
    return str(db.execute("SELECT state FROM link_result").fetchone()[0])


def seed(setup: tuple[sqlite3.Connection, TokenStore, Client]) -> str:
    db, store, client = setup
    evidence = ingest_poll(db, store, flow_id=FLOW, poll=client.poll, now=NOW)
    return evidence.tokens[0].result_id


def test_poll_to_durable_item_and_repeated_poll_never_resends(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
) -> None:
    db, store, client = setup
    assert run(setup) == WorkerOutcome(polled=True, finalized=1)
    assert state(db) == "EXCHANGED"
    rid, item_id, ref, claimed, attempts = db.execute(
        "SELECT result_id, item_id, secret_ref, exchange_claimed_at, exchange_attempts "
        "FROM link_result"
    ).fetchone()
    assert item_id == ITEM and claimed == STAMP and attempts == 1
    assert ref == "access-token." + rid and store.get(ref).reveal() == ACCESS
    assert db.execute("SELECT request_id FROM link_result_attempt").fetchone() == (REQUEST,)
    assert db.execute("SELECT status FROM item").fetchone() == ("DEGRADED",)
    assert run(setup) == WorkerOutcome(polled=True)
    assert client.exchanges == [PUBLIC]
    dump = "\n".join(db.iterdump())
    assert all(secret not in dump for secret in (LINK, PUBLIC, ACCESS))


@pytest.mark.parametrize("same_item", [False, True])
def test_multiple_results_keep_all_credentials_and_count_returned_identity(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    same_item: bool,
) -> None:
    db, store, client = setup
    client.same_item = same_item
    a, b = session(), session("second-session", "synthetic-second-public")
    client.poll = LinkSessionPoll(LinkSessionShape.ITEM_ADDED, (a, b, a))
    assert run(setup).finalized == 2
    assert len(client.exchanges) == 2
    assert db.execute("SELECT count(*) FROM item").fetchone()[0] == (1 if same_item else 2)
    assert db.execute("SELECT count(*) FROM link_result WHERE state='EXCHANGED'").fetchone()[0] == 2
    assert len(list(store.directory.glob("access-token.*.json"))) == 2
    client.poll = LinkSessionPoll(LinkSessionShape.ITEM_ADDED, (b, a))
    assert run(setup).finalized == 0
    assert len(client.exchanges) == 2
    read_item_budget(db)


@pytest.mark.parametrize(
    "shape",
    [
        LinkSessionShape.SESSIONS_ABSENT,
        LinkSessionShape.SESSIONS_NULL,
        LinkSessionShape.NO_SESSIONS,
        LinkSessionShape.SESSION_IN_PROGRESS,
    ],
)
def test_not_ready_shapes_never_invent_result_or_item(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    shape: LinkSessionShape,
) -> None:
    db, _, client = setup
    sessions = (
        (LinkSessionRecord("synthetic-session", NOW),)
        if shape == LinkSessionShape.SESSION_IN_PROGRESS
        else ()
    )
    client.poll = LinkSessionPoll(shape, sessions)
    assert run(setup) == WorkerOutcome(polled=True)
    assert not client.exchanges
    assert db.execute("SELECT count(*) FROM link_result").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM item").fetchone() == (0,)
    assert db.execute("SELECT state, polling_closed_at FROM link_request").fetchone() == (
        "URL_MINTED",
        None,
    )


@pytest.mark.parametrize(
    "finished, now, expected",
    [
        (None, NOW, "SUCCESS_PENDING_EXCHANGE"),
        (NOW, NOW + timedelta(minutes=30), "TOKEN_EXPIRED"),
        (NOW, NOW + timedelta(minutes=31), "TOKEN_EXPIRED"),
        (NOW, NOW + timedelta(minutes=29, seconds=59), "EXCHANGED"),
    ],
)
def test_only_observed_finish_controls_exact_exchange_deadline(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    finished: datetime | None,
    now: datetime,
    expected: str,
) -> None:
    db, _, client = setup
    client.poll = LinkSessionPoll(LinkSessionShape.ITEM_ADDED, (session(finished=finished),))
    run(setup, now)
    assert state(db) == expected
    assert bool(client.exchanges) == (expected == "EXCHANGED")


def test_deadline_is_rechecked_after_slow_poll_and_before_each_send(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, store, client = setup
    moment = NOW

    def poll(key: str) -> LinkSessionPoll:
        nonlocal moment
        moment = NOW + timedelta(minutes=30)
        return client.poll

    monkeypatch.setattr(client, "link_token_get", poll)
    run_request(db, store, client, flow_id=FLOW, country_codes=("US",), clock=lambda: moment)
    assert state(db) == "TOKEN_EXPIRED" and not client.exchanges


@pytest.mark.parametrize("prior_state", ["EXCHANGE_UNCERTAIN", "TOKEN_EXPIRED", "EXCHANGED"])
def test_zero_row_claim_never_calls_provider(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    prior_state: str,
) -> None:
    db, _, client = setup
    seed(setup)
    db.execute("UPDATE link_result SET state = ?", (prior_state,))
    db.commit()
    run(setup)
    assert not client.exchanges and state(db) == prior_state


@pytest.mark.parametrize("crash_state", ["EXCHANGING", "SUCCESS_PENDING_EXCHANGE"])
def test_stale_sent_claim_without_material_is_uncertain_and_never_retried(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    crash_state: str,
) -> None:
    db, _, client = setup
    seed(setup)
    db.execute("UPDATE link_result SET state = ?, exchange_attempts = 1", (crash_state,))
    db.commit()
    run(setup)
    run(setup)
    assert state(db) == "EXCHANGE_UNCERTAIN" and not client.exchanges


@pytest.mark.parametrize("at", ["before-send", "after-send", "before-capture", "after-fsync"])
def test_restart_at_exchange_crash_boundaries_never_sends_twice(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    at: str,
) -> None:
    db, store, client = setup
    original_put = store.put
    with monkeypatch.context() as patch:
        if at == "before-send":
            patch.setattr(
                "networth.link_worker._exchange", lambda *a, **k: (_ for _ in ()).throw(Crash())
            )
        elif at == "after-send":
            client.exchange_error = Crash()
        elif at == "before-capture":
            patch.setattr(
                "networth.link_worker._capture", lambda *a, **k: (_ for _ in ()).throw(Crash())
            )
        else:

            def put(*args: Any, **kwargs: Any) -> str:
                original_put(*args, **kwargs)
                raise Crash()

            patch.setattr(store, "put", put)
        with pytest.raises(Crash):
            run(setup)
    assert state(db) == "EXCHANGING"
    assert db.execute("SELECT attempt_number FROM link_result_attempt").fetchone() == (1,)
    calls = len(client.exchanges)
    client.exchange_error = None
    restarted = sqlite3.connect(tmp_path / "synthetic.sqlite")
    try:
        run((restarted, TokenStore(store.directory), client))
        assert len(client.exchanges) == calls
        assert state(restarted) == ("EXCHANGED" if at == "after-fsync" else "EXCHANGE_UNCERTAIN")
    finally:
        restarted.close()


def test_identifiers_precede_put_and_storage_fault_is_persistent_hold(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, store, client = setup

    def put(*a: Any, **k: Any) -> str:
        assert db.execute("SELECT item_id FROM link_result").fetchone() == (ITEM,)
        assert db.execute("SELECT request_id FROM link_result_attempt").fetchone() == (REQUEST,)
        assert not db.in_transaction
        raise OSError(ACCESS)

    with monkeypatch.context() as patch:
        patch.setattr(store, "put", put)
        assert run(setup).held
    assert state(db) == "EXCHANGING"
    assert run(setup).held and client.exchanges == [PUBLIC]
    assert ACCESS not in "\n".join(db.iterdump())


def test_metadata_outage_restart_finishes_from_material_without_poll_or_exchange(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    tmp_path: Path,
) -> None:
    db, store, client = setup
    client.metadata_error = PlaidCallError("synthetic institution sentinel")
    assert run(setup).failed and state(db) == "EXCHANGING"
    assert db.execute("SELECT count(*) FROM item").fetchone() == (0,)
    client.metadata_error = None
    client.poll_error = PlaidCallError(LINK)
    restarted = sqlite3.connect(tmp_path / "synthetic.sqlite")
    try:
        outcome = run((restarted, TokenStore(store.directory), client))
        assert outcome.finalized == 1 and outcome.failed
        assert state(restarted) == "EXCHANGED" and client.exchanges == [PUBLIC]
    finally:
        restarted.close()


@pytest.mark.parametrize(
    "error",
    [
        PlaidCallError("synthetic failure", request_id=REQUEST, item_id=ITEM),
        OSError("synthetic raw response"),
    ],
)
def test_exchange_exception_is_terminal_with_available_support_ids(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    error: Exception,
) -> None:
    db, _, client = setup
    client.exchange_error = error
    assert run(setup).failed
    run(setup)
    assert state(db) == "EXCHANGE_UNCERTAIN" and client.exchanges == [PUBLIC]
    expected = ITEM if isinstance(error, PlaidCallError) else None
    assert db.execute("SELECT item_id FROM link_result").fetchone() == (expected,)
    reference = REQUEST if isinstance(error, PlaidCallError) else None
    assert db.execute("SELECT request_id FROM link_result_attempt").fetchone() == (reference,)


@pytest.mark.parametrize("identity", [ACCESS, PUBLIC, LINK, ""])
def test_credential_shaped_item_id_is_never_persisted_or_passed_to_put(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    identity: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, store, client = setup
    client.response = ExchangedItem(ACCESS, identity, ACCESS)

    def forbidden(*a: Any, **k: Any) -> str:
        pytest.fail("invalid Item identity reached put")

    monkeypatch.setattr(store, "put", forbidden)
    assert run(setup).failed
    assert state(db) == "EXCHANGE_UNCERTAIN"
    assert db.execute("SELECT item_id FROM link_result").fetchone() == (None,)
    assert db.execute("SELECT request_id FROM link_result_attempt").fetchone() == (None,)
    assert all(s not in "\n".join(db.iterdump()) for s in (ACCESS, PUBLIC, LINK))


@pytest.mark.parametrize("hold", ["material", "observation", "unverified"])
def test_holds_block_automatic_exchange_across_restarts(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    hold: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, store, client = setup
    if hold == "material":
        db.execute(
            "INSERT INTO link_material_hold(hold_id, flow_id, reason, observed_at) "
            "VALUES (?, ?, 'ATTRIBUTION_AMBIGUOUS', ?)",
            ("3" * 32, FLOW, STAMP),
        )
        db.commit()
    elif hold == "observation":
        client.poll = LinkSessionPoll(
            LinkSessionShape.ITEM_ADDED,
            (
                session(),
                LinkSessionRecord(None, public_tokens=("synthetic-other",), item_add_results=1),
            ),
        )
    else:

        def broken(flow_id: str) -> None:
            raise UnverifiedMaterial(ACCESS)

        monkeypatch.setattr(store, "reconcile", broken)
    assert run(setup).held
    assert run(setup).held
    assert not client.exchanges


def test_failed_poll_preserves_request_and_clocks_without_claiming_no_success(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
) -> None:
    db, _, client = setup
    client.poll_error = PlaidCallError(LINK)
    assert run(setup, NOW + timedelta(days=1)).failed
    assert db.execute(
        "SELECT state, last_poll_at, poll_error, material_reaped_at FROM link_request"
    ).fetchone() == ("URL_MINTED", None, "POLL_FAILED", None)
    assert not client.exchanges


def test_request_lock_is_cross_process_and_held_through_send_and_storage(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, store, client = setup
    lock = request_lock_path(db, FLOW)
    probe = "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
    original = client.item_public_token_exchange
    original_put = store.put

    def check() -> None:
        result = subprocess.run([sys.executable, "-c", probe, str(lock)], capture_output=True)
        assert result.returncode != 0 and b"BlockingIOError" in result.stderr

    def send(token: str) -> ExchangedItem:
        check()
        return original(token)

    def put(*a: Any, **k: Any) -> str:
        check()
        return original_put(*a, **k)

    monkeypatch.setattr(client, "item_public_token_exchange", send)
    monkeypatch.setattr(store, "put", put)
    assert run(setup).finalized == 1
    assert (
        subprocess.run([sys.executable, "-c", probe, str(lock)], capture_output=True).returncode
        == 0
    )


def test_competing_process_skips_locked_request_without_poll_or_exchange(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
) -> None:
    db, _, client = setup
    code = (
        "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); "
        "print('locked',flush=True); sys.stdin.read()"
    )
    with subprocess.Popen(
        [sys.executable, "-c", code, str(request_lock_path(db, FLOW))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        assert process.stdout and process.stdin
        assert process.stdout.readline().strip() == "locked"
        try:
            assert run(setup) == WorkerOutcome(busy=True)
            assert client.polls == 0 and not client.exchanges
        finally:
            process.stdin.close()
            process.wait(timeout=5)


def test_worker_refuses_active_transaction_and_naive_clock(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
) -> None:
    db, _, client = setup
    db.execute("BEGIN")
    with pytest.raises(WorkerError, match="transaction"):
        run(setup)
    db.rollback()
    with pytest.raises(WorkerError, match="aware"):
        run(setup, NOW.replace(tzinfo=None))
    assert not client.exchanges and not client.polls


def test_cli_production_refuses_before_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("NETWORTH_ENV", "production")

    def forbidden(*a: Any, **k: Any) -> None:
        pytest.fail("Production credentials were requested")

    monkeypatch.setattr("networth.commands.poll_link.load_credentials", forbidden)
    assert main(["poll-link", "--country-code", "US"]) == 2
    assert "Sandbox only" in capsys.readouterr().err


def test_cli_runs_sandbox_and_redacts_failures(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, store, client = setup
    monkeypatch.setenv("NETWORTH_ENV", "sandbox")
    monkeypatch.setattr(
        "networth.commands.poll_link.paths_for",
        lambda e: Paths(
            credentials=tmp_path / "unused",
            items=store.directory,
            database=tmp_path / "synthetic.sqlite",
        ),
    )
    monkeypatch.setattr("networth.commands.poll_link.load_credentials", lambda e: object())
    monkeypatch.setattr("networth.commands.poll_link.PlaidClient", lambda c: client)
    client.poll_error = OSError(ACCESS)
    assert main(["poll-link", "--flow", FLOW, "--country-code", "US"]) == 1
    captured = capsys.readouterr()
    assert ACCESS not in captured.out + captured.err


def test_previously_observed_result_expires_even_when_current_poll_has_no_tokens(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
) -> None:
    db, _, client = setup
    seed(setup)
    client.poll = LinkSessionPoll(LinkSessionShape.SESSIONS_ABSENT)
    run(setup, NOW + timedelta(minutes=30))
    assert state(db) == "TOKEN_EXPIRED" and not client.exchanges


def test_each_result_uses_its_own_deadline(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
) -> None:
    db, _, client = setup
    client.poll = LinkSessionPoll(
        LinkSessionShape.ITEM_ADDED,
        (
            session(finished=NOW - timedelta(minutes=30)),
            session("synthetic-later", "synthetic-later-public", NOW),
        ),
    )
    assert run(setup).finalized == 1
    assert client.exchanges == ["synthetic-later-public"]
    assert {row[0] for row in db.execute("SELECT state FROM link_result")} == {
        "TOKEN_EXPIRED",
        "EXCHANGED",
    }


@pytest.mark.parametrize("pending", [False, True])
def test_legacy_material_is_finalized_without_exchange_despite_observation_hold(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    pending: bool,
) -> None:
    db, store, client = setup
    rid = seed(setup)
    cur = db.execute(
        "INSERT INTO link_flow(flow_id, secret_ref, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, ?, 'EXCHANGING')",
        (FLOW, "link-token." + FLOW, STAMP, STAMP),
    )
    db.execute("UPDATE link_request SET legacy_link_flow_id = ?", (cur.lastrowid,))
    db.execute(
        "UPDATE link_result SET legacy_link_flow_id = ?, token_digest = NULL, state = 'EXCHANGING'",
        (cur.lastrowid,),
    )
    db.commit()
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, ACCESS, item_id=ITEM)
    if pending:
        (store.directory / (ref + ".json")).rename(store.directory / ("." + ref + ".pending"))
    outcome = run(setup)
    assert outcome.finalized == 1 and outcome.held
    assert state(db) == "EXCHANGED" and not client.exchanges
    assert db.execute(
        "SELECT secret_ref FROM link_result WHERE result_id = ?", (rid,)
    ).fetchone() == (ref,)


def test_unverified_material_found_during_finalization_is_a_persistent_hold(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, store, client = setup
    rid = seed(setup)
    store.put(SecretKind.ACCESS_TOKEN, rid, ACCESS, item_id=ITEM)
    original = store.reconcile
    count = 0

    def reconcile(flow_id: str) -> Any:
        nonlocal count
        if flow_id == rid:
            count += 1
            if count == 2:
                raise UnverifiedMaterial(ACCESS)
        return original(flow_id)

    with monkeypatch.context() as patch:
        patch.setattr(store, "reconcile", reconcile)
        assert run(setup).held
    assert run(setup).held and not client.exchanges
    assert state(db) == "EXCHANGING"


def test_capture_transaction_failure_never_retries_exchange_or_stores_material(
    setup: tuple[sqlite3.Connection, TokenStore, Client],
) -> None:
    db, store, client = setup
    db.execute(
        "CREATE TRIGGER synthetic_capture_failure BEFORE UPDATE OF item_id ON link_result "
        "WHEN NEW.item_id IS NOT NULL BEGIN SELECT RAISE(ABORT, 'synthetic-sensitive'); END"
    )
    with pytest.raises(WorkerError) as caught:
        run(setup)
    assert "synthetic-sensitive" not in str(caught.value)
    assert not list(store.directory.glob("access-token.*.json"))
    db.execute("DROP TRIGGER synthetic_capture_failure")
    run(setup)
    assert state(db) == "EXCHANGE_UNCERTAIN" and client.exchanges == [PUBLIC]
