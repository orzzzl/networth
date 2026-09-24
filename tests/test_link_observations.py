"""Synthetic polling evidence exercises the real SQLite/TokenStore boundary."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from networth.cli import main
from networth.item_budget import ItemBudgetError, read_item_budget
from networth.link_observations import (
    IngestedPoll,
    ObservationError,
    adjudicate_observation,
    ingest_poll,
)
from networth.plaid.client import LinkSessionPoll, LinkSessionRecord, LinkSessionShape
from networth.plaid.environment import Paths
from networth.storage import migrate
from networth.tokenstore import InvalidSecretRef, SecretKind, TokenStore, UnverifiedMaterial

FLOW = "1" * 32
AUDIT = "2" * 32
NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
STAMP = "2026-09-23T12:00:00Z"
KEY = "synthetic-link-material-sentinel"
TOKEN = "synthetic-public-material-sentinel"
SECOND = "synthetic-second-material-sentinel"


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[sqlite3.Connection, TokenStore]]:
    db = sqlite3.connect(tmp_path / "synthetic.sqlite")
    migrate(db)
    store = TokenStore(tmp_path / "tokens")
    ref = store.put(SecretKind.LINK_TOKEN, FLOW, KEY)
    db.execute(
        "INSERT INTO link_request(flow_id, secret_ref, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, ?, 'URL_MINTED')",
        (FLOW, ref, STAMP, STAMP),
    )
    db.commit()
    yield db, store
    db.close()


def session(
    sid: str | None = "synthetic-session",
    *,
    tokens: tuple[str, ...] = (TOKEN,),
    count: int | None = None,
    finished: datetime | None = NOW,
) -> LinkSessionRecord:
    return LinkSessionRecord(
        sid, NOW - timedelta(minutes=2), finished, tokens, len(tokens) if count is None else count
    )


def ingest(
    setup: tuple[sqlite3.Connection, TokenStore],
    *sessions: LinkSessionRecord,
    shape: LinkSessionShape = LinkSessionShape.ITEM_ADDED,
) -> IngestedPoll:
    return ingest_poll(
        setup[0], setup[1], flow_id=FLOW, poll=LinkSessionPoll(shape, tuple(sessions)), now=NOW
    )


def resolve(db: sqlite3.Connection, oid: str, slots: int = 0) -> None:
    adjudicate_observation(
        db, observation_id=oid, additional_slots=slots, audit_id=AUDIT, now=NOW, reviewed=True
    )


def test_reordered_duplicate_poll_and_restart_keep_independent_results(
    setup: tuple[sqlite3.Connection, TokenStore],
    tmp_path: Path,
) -> None:
    db, store = setup
    a = session("a", tokens=(TOKEN, TOKEN))
    b = session("b", tokens=(SECOND,), finished=NOW + timedelta(minutes=1))
    first = ingest(setup, a, b)
    assert len(first.tokens) == 2
    ids = {t.result_id for t in first.tokens}
    assert {t.public_token.reveal() for t in first.tokens} == {TOKEN, SECOND}
    assert first.observation_ids == ()
    restarted = sqlite3.connect(tmp_path / "synthetic.sqlite")
    try:
        replay = ingest((restarted, TokenStore(store.directory)), b, a)
        assert {t.result_id for t in replay.tokens} == ids
        assert restarted.execute("SELECT count(*) FROM link_result").fetchone()[0] == 2
        assert read_item_budget(restarted).spent_count == 2
        assert restarted.execute(
            "SELECT token_exchange_expires_at FROM link_result ORDER BY link_session_id"
        ).fetchall() == [("2026-09-23T12:30:00Z",), ("2026-09-23T12:31:00Z",)]
    finally:
        restarted.close()
    dump = "\n".join(db.iterdump())
    for secret in (KEY, TOKEN, SECOND):
        assert secret not in dump + repr(first)
        assert hashlib.sha256(secret.encode()).hexdigest() not in dump
    assert db.execute("SELECT item_id FROM link_result").fetchall() == [(None,), (None,)]


@pytest.mark.parametrize(
    "shape",
    [
        LinkSessionShape.SESSIONS_ABSENT,
        LinkSessionShape.SESSIONS_NULL,
        LinkSessionShape.NO_SESSIONS,
    ],
)
def test_empty_shapes_never_prove_cleanup(
    setup: tuple[sqlite3.Connection, TokenStore],
    shape: LinkSessionShape,
) -> None:
    assert ingest(setup, shape=shape) == IngestedPoll((), ())
    db, store = setup
    assert db.execute(
        "SELECT state, polling_closed_at, material_reaped_at FROM link_request"
    ).fetchone() == (
        "URL_MINTED",
        None,
        None,
    )
    assert read_item_budget(db).spent_count == 0
    assert store.get("link-token." + FLOW).reveal() == KEY


def test_exit_cannot_erase_other_success_or_its_own_later_success(
    setup: tuple[sqlite3.Connection, TokenStore],
) -> None:
    db, _ = setup
    ingest(setup, session("exited", tokens=()), session("active", tokens=(), finished=None))
    assert db.execute("SELECT state FROM link_session ORDER BY link_session_id").fetchall() == [
        ("SESSION_STARTED",),
        ("SESSION_EXITED",),
    ]
    ingest(setup, session("exited"), session("active", tokens=(), finished=None))
    ingest(setup, session("exited", tokens=()))
    assert (
        db.execute("SELECT state FROM link_session WHERE link_session_id='exited'").fetchone()[0]
        == "SESSION_STARTED"
    )
    assert read_item_budget(db).spent_count == 1


def test_finish_arriving_without_token_fills_its_results_deadlines_only(
    setup: tuple[sqlite3.Connection, TokenStore],
) -> None:
    db, _ = setup
    ingest(setup, session(finished=None), session("other", tokens=(SECOND,), finished=None))
    assert db.execute("SELECT DISTINCT token_exchange_expires_at FROM link_result").fetchall() == [
        (None,)
    ]
    ingest(setup, session(tokens=(), count=1))
    assert db.execute(
        "SELECT token_exchange_expires_at, session_retention_expires_at FROM link_result "
        "WHERE link_session_id='synthetic-session'"
    ).fetchone() == ("2026-09-23T12:30:00Z", "2026-09-23T18:00:00Z")
    assert (
        db.execute(
            "SELECT token_exchange_expires_at FROM link_result WHERE link_session_id='other'"
        ).fetchone()[0]
        is None
    )
    ingest(setup, session(finished=None))
    assert (
        db.execute(
            "SELECT finished_at FROM link_session WHERE link_session_id='synthetic-session'"
        ).fetchone()[0]
        == STAMP
    )


@pytest.mark.parametrize("fault", ["absent", "missing-ref", "reaped", "unverified", "corrupt"])
def test_unavailable_key_mints_no_results_and_hold_survives_restoration(
    setup: tuple[sqlite3.Connection, TokenStore],
    monkeypatch: Any,
    fault: str,
) -> None:
    db, store = setup
    ref = "link-token." + FLOW
    if fault == "absent":
        store.delete(ref)
    elif fault == "missing-ref":
        db.execute("UPDATE link_request SET secret_ref=NULL")
        db.commit()
    elif fault == "reaped":
        db.execute("UPDATE link_request SET material_reaped_at=?", (STAMP,))
        db.commit()
    elif fault == "corrupt":
        (store.directory / f"{ref}.json").write_text("synthetic corrupt material")
    else:

        def unverified(ref: str) -> None:
            raise UnverifiedMaterial("synthetic fault")

        monkeypatch.setattr(store, "get", unverified)
    observed = ingest(setup, session(tokens=(TOKEN, SECOND)))
    assert observed.tokens == ()
    assert len(observed.observation_ids) == 1
    assert db.execute("SELECT count(*) FROM link_result").fetchone()[0] == 0
    assert (
        ingest(setup, session(tokens=(SECOND, TOKEN))).observation_ids == observed.observation_ids
    )
    assert db.execute(
        "SELECT reason, max_reported_results FROM link_success_observation"
    ).fetchone() == (
        "DIGEST_KEY_UNAVAILABLE",
        2,
    )
    with pytest.raises(ItemBudgetError, match="adjudication"):
        read_item_budget(db)
    # Recovering a key does not silently resolve existing evidence.
    monkeypatch.undo()
    store.delete(ref)
    store.put(SecretKind.LINK_TOKEN, FLOW, KEY)
    db.execute("UPDATE link_request SET secret_ref=?, material_reaped_at=NULL", (ref,))
    db.commit()
    ingest(setup, session())
    with pytest.raises(ItemBudgetError):
        read_item_budget(db)


def test_missing_identity_groups_unresolved_sets_and_preserves_all_holds(
    setup: tuple[sqlite3.Connection, TokenStore],
) -> None:
    db, _ = setup
    first = ingest(setup, session(None), session("missing-token", tokens=(), count=2))
    assert first.tokens == ()
    assert len(first.observation_ids) == 2
    replay = ingest(setup, session("missing-token", tokens=(), count=3), session(None))
    assert replay.observation_ids == first.observation_ids
    assert (
        db.execute(
            "SELECT max_reported_results FROM link_success_observation "
            "WHERE reason='MISSING_PUBLIC_TOKEN'"
        ).fetchone()[0]
        == 3
    )
    resolve(db, first.observation_ids[0], 0)
    with pytest.raises(ItemBudgetError):
        read_item_budget(db)
    resolve(db, first.observation_ids[1], 2)
    assert read_item_budget(db).spent_count == 2
    assert len(read_item_budget(db).stranded) == 2
    with pytest.raises(ObservationError, match="already adjudicated"):
        resolve(db, first.observation_ids[1], 0)
    # Equal-looking ambiguous evidence is not proof that it is the adjudicated set.
    new = ingest(setup, session(None))
    assert set(new.observation_ids) == set(first.observation_ids)
    with pytest.raises(ItemBudgetError):
        read_item_budget(db)
    assert db.execute("SELECT count(*) FROM link_observation_adjudication").fetchone()[0] == 2


def test_legacy_without_digest_cannot_be_mistaken_for_new_slot(
    setup: tuple[sqlite3.Connection, TokenStore],
) -> None:
    db, _ = setup
    db.execute(
        "INSERT INTO link_flow(flow_id, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, 'EXCHANGING')",
        (FLOW, STAMP, STAMP),
    )
    db.execute(
        "INSERT INTO link_result(result_id, flow_id, legacy_link_flow_id, state) "
        "VALUES (?, ?, 1, 'EXCHANGING')",
        (AUDIT, FLOW),
    )
    db.commit()
    result = ingest(setup, session())
    assert result.tokens == ()
    assert db.execute("SELECT count(*) FROM link_result").fetchone()[0] == 1
    assert (
        db.execute("SELECT reason FROM link_success_observation").fetchone()[0]
        == "LEGACY_ATTRIBUTION_AMBIGUOUS"
    )
    with pytest.raises(ItemBudgetError):
        read_item_budget(db)


@pytest.mark.parametrize(
    "fault", ["naive", "session-material", "changed-clock", "session-conflict", "sql"]
)
def test_refusals_are_atomic_and_redacted(
    setup: tuple[sqlite3.Connection, TokenStore],
    fault: str,
) -> None:
    db, _ = setup
    ingest(setup, session())
    before = "\n".join(db.iterdump())
    candidate = session("new", tokens=(SECOND,))
    if fault == "naive":
        candidate = session("new", finished=NOW.replace(tzinfo=None))
    elif fault == "session-material":
        candidate = session(SECOND, tokens=(SECOND,))
    elif fault == "changed-clock":
        candidate = session(finished=NOW + timedelta(seconds=1))
    elif fault == "session-conflict":
        candidate = session("new")
    else:
        db.execute(
            "CREATE TRIGGER fault BEFORE INSERT ON link_result BEGIN "
            "SELECT RAISE(ABORT, 'synthetic-public-material-sentinel'); END"
        )
        db.commit()
        before = "\n".join(db.iterdump())
    with pytest.raises(ObservationError) as exc:
        ingest(setup, candidate)
    assert TOKEN not in str(exc.value)
    assert SECOND not in str(exc.value)
    assert "\n".join(db.iterdump()) == before
    assert not db.in_transaction


def test_adjudication_confirmation_and_cli_environment_are_required(
    setup: tuple[sqlite3.Connection, TokenStore],
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    db, _ = setup
    oid = ingest(setup, session(None)).observation_ids[0]
    with pytest.raises(ObservationError, match="reviewed"):
        adjudicate_observation(
            db, observation_id=oid, additional_slots=0, audit_id=AUDIT, now=NOW, reviewed=False
        )
    monkeypatch.delenv("NETWORTH_ENV", raising=False)
    args = [
        "adjudicate-link-observation",
        "--observation",
        oid,
        "--additional-slots",
        "1",
        "--audit-id",
        AUDIT,
    ]
    assert main(args) == 2
    assert main([*args, "--confirm-reviewed"]) == 2
    monkeypatch.setenv("NETWORTH_ENV", "sandbox")
    monkeypatch.setattr(
        "networth.commands.adjudicate_link_observation.paths_for",
        lambda env: Paths(
            tmp_path / "unused",
            tmp_path / "unused",
            tmp_path / "synthetic.sqlite",
        ),
    )
    assert main([*args, "--confirm-reviewed"]) == 0
    assert read_item_budget(db).spent_count == 1
    assert (
        db.execute("SELECT resolution_note FROM link_success_observation").fetchone()[0]
        == "Reviewed private evidence " + AUDIT
    )
    assert TOKEN not in capsys.readouterr().out


@pytest.mark.parametrize("slots", [-1, True])
def test_invalid_adjudication_count_cannot_change_evidence(
    setup: tuple[sqlite3.Connection, TokenStore],
    slots: int,
) -> None:
    db, _ = setup
    oid = ingest(setup, session(None)).observation_ids[0]
    with pytest.raises(ObservationError):
        resolve(db, oid, slots)
    assert db.execute("SELECT resolved_at FROM link_success_observation").fetchone()[0] is None


def test_recovered_identity_reopens_adjudication_without_double_counting(
    setup: tuple[sqlite3.Connection, TokenStore],
) -> None:
    db, store = setup
    ref = "link-token." + FLOW
    store.delete(ref)
    oid = ingest(setup, session()).observation_ids[0]
    resolve(db, oid, 1)
    assert read_item_budget(db).spent_count == 1
    store.put(SecretKind.LINK_TOKEN, FLOW, KEY)
    recovered = ingest(setup, session())
    assert len(recovered.tokens) == 1
    assert recovered.observation_ids == (oid,)
    with pytest.raises(ItemBudgetError):
        read_item_budget(db)
    resolve(db, oid, 0)  # This slot is now counted by the identified result.
    assert read_item_budget(db).spent_count == 1
    assert db.execute(
        "SELECT additional_slots FROM link_observation_adjudication ORDER BY id"
    ).fetchall() == [(1,), (0,)]


def test_pending_key_and_equivalent_legacy_clock_are_usable(
    setup: tuple[sqlite3.Connection, TokenStore],
) -> None:
    db, store = setup
    ref = "link-token." + FLOW
    (store.directory / f"{ref}.json").rename(store.directory / f".{ref}.pending")
    ingest(setup, session())
    db.execute("UPDATE link_session SET finished_at = '2026-09-23T12:00:00+00:00'")
    db.commit()
    assert len(ingest(setup, session()).tokens) == 1


def test_adjudication_ledger_failure_rolls_back_current_resolution(
    setup: tuple[sqlite3.Connection, TokenStore],
) -> None:
    db, _ = setup
    oid = ingest(setup, session(None)).observation_ids[0]
    db.execute(
        "CREATE TRIGGER ledger_fault BEFORE INSERT ON link_observation_adjudication "
        "BEGIN SELECT RAISE(ABORT, 'synthetic-public-material-sentinel'); END"
    )
    db.commit()
    with pytest.raises(ObservationError) as exc:
        resolve(db, oid, 0)
    assert TOKEN not in str(exc.value)
    with pytest.raises(ItemBudgetError):
        read_item_budget(db)
    assert db.execute("SELECT count(*) FROM link_observation_adjudication").fetchone()[0] == 0


def test_migration_preserves_prior_adjudication_when_reopening(tmp_path: Path) -> None:
    from importlib import resources

    db = sqlite3.connect(":memory:")
    for file in sorted(resources.files("networth.storage.sql").iterdir(), key=lambda f: f.name):
        if file.name.endswith(".sql") and int(file.name[:4]) <= 6:
            db.executescript(file.read_text())
    db.execute("PRAGMA user_version=6")
    store = TokenStore(tmp_path / "old-tokens")
    ref = store.put(SecretKind.LINK_TOKEN, FLOW, KEY)
    db.execute(
        "INSERT INTO link_request(flow_id, secret_ref, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, ?, 'URL_MINTED')",
        (FLOW, ref, STAMP, STAMP),
    )
    db.execute(
        "INSERT INTO link_success_observation VALUES (?, ?, NULL, 'MISSING_SESSION_ID', "
        "?, ?, 'Synthetic prior review', 1)",
        (AUDIT, FLOW, STAMP, STAMP),
    )
    db.commit()
    assert migrate(db) == (7, 8, 9)
    assert (
        db.execute("SELECT resolution_note FROM link_observation_adjudication").fetchone()[0]
        == "Synthetic prior review"
    )
    assert ingest((db, store), session()).observation_ids == (AUDIT,)
    with pytest.raises(ItemBudgetError):
        read_item_budget(db)
    assert (
        db.execute("SELECT max_reported_results FROM link_success_observation").fetchone()[0]
        is None
    )
    assert (
        db.execute("SELECT resolution_note FROM link_observation_adjudication").fetchone()[0]
        == "Synthetic prior review"
    )
    db.close()


def test_same_token_under_different_request_keys_has_distinct_digests(
    setup: tuple[sqlite3.Connection, TokenStore],
) -> None:
    db, store = setup
    original = ingest(setup, session())
    ref = store.put(SecretKind.LINK_TOKEN, AUDIT, "synthetic-other-link-key")
    db.execute(
        "INSERT INTO link_request(flow_id, secret_ref, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, ?, 'URL_MINTED')",
        (AUDIT, ref, STAMP, STAMP),
    )
    db.commit()
    other = ingest_poll(
        db,
        store,
        flow_id=AUDIT,
        poll=LinkSessionPoll(LinkSessionShape.ITEM_ADDED, (session(),)),
        now=NOW,
    )
    assert other.tokens[0].result_id != original.tokens[0].result_id
    assert db.execute("SELECT count(DISTINCT token_digest) FROM link_result").fetchone()[0] == 2


def test_unallowlisted_poll_fields_never_reach_storage(
    setup: tuple[sqlite3.Connection, TokenStore],
    capsys: Any,
    caplog: Any,
) -> None:
    class ExtraFields(LinkSessionRecord):
        accounts = "synthetic-account-sentinel"
        institution = "synthetic-institution-sentinel"

    candidate = ExtraFields("synthetic-session", NOW, NOW, (TOKEN,), 1)
    outcome = ingest(setup, candidate)
    output = capsys.readouterr()
    text = "\n".join(setup[0].iterdump()) + output.out + output.err + caplog.text + repr(outcome)
    for sentinel in (candidate.accounts, candidate.institution, TOKEN, KEY):
        assert sentinel not in text


# "2" * 32 has no uppercase form, so the case arm needs a hex id that has letters.
MALFORMED = ("synthetic free-form review note", "", ("ab" * 16).upper(), AUDIT + "0")


def test_adjudication_refuses_free_form_audit_and_observation_identifiers(
    setup: tuple[sqlite3.Connection, TokenStore],
) -> None:
    """`audit_id` names a private record by minted UUID; it is not an evidence field.

    Without the refusal the operator's argument is concatenated straight into
    `resolution_note`, which is how free-form text — a pasted credential among
    it — reaches SQLite through a command whose whole contract is that it
    carries none.
    """
    db, _ = setup
    oid = ingest(setup, session(None)).observation_ids[0]
    for audit in MALFORMED:
        with pytest.raises((ObservationError, InvalidSecretRef)):
            adjudicate_observation(
                db,
                observation_id=oid,
                additional_slots=0,
                audit_id=audit,
                now=NOW,
                reviewed=True,
            )
    for observation in MALFORMED:
        with pytest.raises((ObservationError, InvalidSecretRef)):
            adjudicate_observation(
                db,
                observation_id=observation,
                additional_slots=0,
                audit_id=AUDIT,
                now=NOW,
                reviewed=True,
            )
    assert db.execute("SELECT resolved_at FROM link_success_observation").fetchone()[0] is None
    assert db.execute("SELECT count(*) FROM link_observation_adjudication").fetchone()[0] == 0


def test_unidentified_success_is_held_even_when_the_reply_counts_no_results(
    setup: tuple[sqlite3.Connection, TokenStore],
) -> None:
    """A token with no session id is spent capacity whatever `item_add_results` says.

    The reply is not trusted to be self-consistent anywhere else in this module
    (`tokens_missing` exists for the other direction), so a carried token must
    not be erasable by a zero count: that reads as unused capacity.
    """
    db, _ = setup
    observed = ingest(setup, session(None, count=0))
    assert observed.tokens == ()
    assert len(observed.observation_ids) == 1
    assert db.execute(
        "SELECT reason, max_reported_results FROM link_success_observation"
    ).fetchone() == ("MISSING_SESSION_ID", 1)
    with pytest.raises(ItemBudgetError, match="adjudication"):
        read_item_budget(db)


def test_tokenless_success_reopens_a_resolved_observation_in_another_session(
    setup: tuple[sqlite3.Connection, TokenStore],
) -> None:
    db, _ = setup
    old = ingest(setup, session(None, tokens=(), count=1)).observation_ids[0]
    resolve(db, old)
    # A different session creates a different hold. Only the top-level reopen
    # predicate can reopen the earlier observation; _hold's upsert cannot.
    ingest(setup, session("later-tokenless-session", tokens=(), count=1))
    assert db.execute(
        "SELECT resolved_at FROM link_success_observation WHERE observation_id = ?", (old,)
    ).fetchone() == (None,)


@pytest.mark.parametrize("reviewed", [False, None, 1, "yes"])
def test_direct_observation_adjudication_requires_literal_confirmation(
    setup: tuple[sqlite3.Connection, TokenStore], reviewed: Any
) -> None:
    db, _ = setup
    old = ingest(setup, session(None)).observation_ids[0]
    with pytest.raises(ObservationError, match="explicit reviewed"):
        adjudicate_observation(
            db, observation_id=old, additional_slots=0, audit_id=AUDIT, now=NOW, reviewed=reviewed
        )
    assert db.execute("SELECT count(*) FROM link_observation_adjudication").fetchone() == (0,)
