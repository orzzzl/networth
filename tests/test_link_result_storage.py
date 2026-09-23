"""07a's result identity and budget migration, using synthetic evidence only."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from importlib import resources

import pytest

from networth.item_budget import (
    _REQUEST_STATES,
    _RESULT_STATES,
    _SESSION_STATES,
    ItemBudgetError,
    SlotEvidence,
    read_item_budget,
)
from networth.storage import migrate

NOW = "2026-09-22T10:00:00Z"
LATER = "2026-09-22T10:30:00Z"
STATES = (*_REQUEST_STATES, *_SESSION_STATES, *_RESULT_STATES)


def old_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    for file in sorted(resources.files("networth.storage.sql").iterdir(), key=lambda p: p.name):
        if file.name.endswith(".sql") and int(file.name[:4]) <= 5:
            connection.executescript(file.read_text())
    connection.execute("PRAGMA user_version = 5")
    return connection


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def request(db: sqlite3.Connection, flow_id: str = "request-a", state: str = "URL_MINTED") -> None:
    db.execute(
        "INSERT INTO link_request(flow_id, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, ?)",
        (flow_id, NOW, LATER, state),
    )


def result(
    db: sqlite3.Connection,
    number: int,
    *,
    flow_id: str = "request-a",
    state: str = "TOKEN_EXPIRED",
    item_id: str | None = None,
) -> str:
    result_id = f"{number:032x}"
    db.execute(
        "INSERT INTO link_result(result_id, flow_id, token_digest, state, item_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (result_id, flow_id, f"{number:064x}", state, item_id),
    )
    return result_id


def item(db: sqlite3.Connection, item_id: str = "synthetic-item") -> None:
    db.execute(
        "INSERT OR IGNORE INTO institution(id, plaid_institution_id, name, is_oauth) "
        "VALUES (1, 'synthetic', 'Synthetic', 0)"
    )
    db.execute(
        "INSERT INTO item(institution_id, plaid_item_id, secret_ref, status, status_since, "
        "created_at) VALUES (1, ?, ?, 'DEGRADED', ?, ?)",
        (item_id, f"synthetic-ref-{item_id}", NOW, NOW),
    )


@pytest.mark.parametrize("state", STATES)
def test_backfill_preserves_legacy_state_identity_clocks_and_attempts(state: str) -> None:
    db = old_database()
    db.execute(
        """INSERT INTO link_flow(
            flow_id, secret_ref, minted_at, hosted_url_expires_at, state,
            link_session_id, item_id, finished_at, token_exchange_expires_at,
            session_retention_expires_at, exchange_claimed_at, exchange_claim_owner,
            exchange_attempts
        ) VALUES ('legacy', 'legacy-link-ref', ?, ?, ?, 'session', 'synthetic-item',
                  ?, ?, ?, ?, 'previous-worker', 2)""",
        (NOW, LATER, state, NOW, LATER, "2026-09-22T16:00:00Z", NOW),
    )
    db.executemany(
        "INSERT INTO link_exchange_attempt VALUES (1, ?, ?)",
        [(1, "synthetic-request-1"), (2, "synthetic-request-2")],
    )
    db.commit()
    assert migrate(db) == (6, 7)
    assert migrate(db) == ()
    assert db.execute("SELECT state FROM link_flow").fetchone() == (state,)
    assert db.execute("SELECT secret_ref, legacy_link_flow_id FROM link_request").fetchone() == (
        "legacy-link-ref",
        1,
    )
    assert db.execute("SELECT link_session_id, finished_at FROM link_session").fetchone() == (
        "session",
        NOW,
    )
    if state in _RESULT_STATES:
        row = db.execute(
            "SELECT result_id, legacy_link_flow_id, state, finished_at, "
            "token_exchange_expires_at, session_retention_expires_at, exchange_claimed_at, "
            "exchange_claim_owner, exchange_attempts FROM link_result"
        ).fetchone()
        assert row is not None
        assert re.fullmatch("[0-9a-f]{32}", row[0])
        assert row[1:] == (1, state, NOW, LATER, "2026-09-22T16:00:00Z", NOW, "previous-worker", 2)
        assert db.execute(
            "SELECT attempt_number, request_id FROM link_result_attempt ORDER BY attempt_number"
        ).fetchall() == [(1, "synthetic-request-1"), (2, "synthetic-request-2")]
        assert read_item_budget(db).spent_count == 1
        assert db.execute("SELECT count(*) FROM link_success_evidence").fetchone() == (1,)
        item(db)
        assert read_item_budget(db).spent_count == 1  # Item + migrated result + legacy parent
    else:
        assert read_item_budget(db).spent_count == 0
        assert db.execute("SELECT count(*) FROM link_result").fetchone() == (0,)
    db.close()


@pytest.mark.parametrize("state", _RESULT_STATES)
def test_imported_legacy_success_counts_until_a_result_carries_its_slot(
    db: sqlite3.Connection, state: str
) -> None:
    legacy_id = db.execute(
        "INSERT INTO link_flow(flow_id, minted_at, hosted_url_expires_at, state) "
        "VALUES ('legacy', ?, ?, ?) RETURNING id",
        (NOW, LATER, state),
    ).fetchone()[0]
    request(db, "legacy")
    db.execute("UPDATE link_request SET legacy_link_flow_id = ?", (legacy_id,))
    assert read_item_budget(db).spent_count == 1
    assert read_item_budget(db).remaining == 9
    assert db.execute("SELECT result_id FROM link_success_evidence").fetchall() == [(None,)]

    result_id = result(db, 1, flow_id="legacy", state=state)
    db.execute("UPDATE link_result SET legacy_link_flow_id = ?", (legacy_id,))
    assert read_item_budget(db).spent_count == 1
    assert db.execute("SELECT result_id FROM link_success_evidence").fetchall() == [(result_id,)]

    db.execute("DELETE FROM link_result WHERE result_id = ?", (result_id,))
    assert read_item_budget(db).spent_count == 1
    assert db.execute("SELECT result_id FROM link_success_evidence").fetchall() == [(None,)]


def test_legacy_missing_session_is_not_fabricated_and_mapping_survives_reentry() -> None:
    db = old_database()
    db.execute(
        "INSERT INTO link_flow(flow_id, minted_at, hosted_url_expires_at, state) "
        "VALUES ('legacy', ?, ?, 'EXCHANGE_UNCERTAIN')",
        (NOW, LATER),
    )
    db.commit()
    migrate(db)
    first = db.execute(
        "SELECT result_id, link_session_id, token_digest FROM link_result"
    ).fetchone()
    assert first is not None and first[1:] == (None, None)
    assert db.execute("SELECT count(*) FROM link_session").fetchone() == (0,)
    assert read_item_budget(db).spent_count == 1
    migrate(db)
    assert (
        db.execute("SELECT result_id, link_session_id, token_digest FROM link_result").fetchone()
        == first
    )
    db.close()


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("link_request", _REQUEST_STATES),
        ("link_session", _SESSION_STATES),
        ("link_result", _RESULT_STATES),
    ],
)
def test_entity_state_classification_is_total(
    db: sqlite3.Connection,
    table: str,
    expected: tuple[str, ...],
) -> None:
    sql = db.execute("SELECT sql FROM sqlite_schema WHERE name = ?", (table,)).fetchone()[0]
    state_check = re.search(r"state IN \((.*?)\)", sql, re.DOTALL)
    assert state_check is not None
    assert set(re.findall(r"'([A-Z_]+)'", state_check[1])) == set(expected)


def test_multiple_results_keep_request_and_result_identity_in_stranded_view(
    db: sqlite3.Connection,
) -> None:
    request(db)
    first = result(db, 1)
    second = result(db, 2, state="EXCHANGE_UNCERTAIN")
    assert db.execute(
        "SELECT result_id, flow_id FROM stranded_link_flow ORDER BY result_id"
    ).fetchall() == [(first, "request-a"), (second, "request-a")]
    budget = read_item_budget(db)
    assert budget.spent_count == 2
    assert {s.result_id for s in budget.stranded} == {first, second}
    assert {s.flow_id for s in budget.stranded} == {"request-a"}


def test_sessions_are_request_scoped_and_result_cannot_name_another_requests_session(
    db: sqlite3.Connection,
) -> None:
    request(db)
    request(db, "request-b")
    db.execute(
        "INSERT INTO link_session VALUES ('request-a', 'session-a', 'SESSION_EXITED', ?, ?)",
        (NOW, LATER),
    )
    result_id = result(db, 1, flow_id="request-b")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute(
            "UPDATE link_result SET link_session_id = 'session-a' WHERE result_id = ?",
            (result_id,),
        )
    db.execute(
        "INSERT INTO link_session VALUES ('request-b', 'session-a', 'SESSION_STARTED', ?, NULL)",
        (NOW,),
    )
    db.execute(
        "UPDATE link_result SET link_session_id = 'session-a' WHERE result_id = ?",
        (result_id,),
    )
    assert read_item_budget(db).spent_count == 1


def test_equal_item_identity_deduplicates_budget_without_erasing_either_credential(
    db: sqlite3.Connection,
) -> None:
    request(db)
    for number in (1, 2):
        result_id = result(db, number, state="EXCHANGED", item_id="synthetic-item")
        db.execute(
            "UPDATE link_result SET secret_ref = ? WHERE result_id = ?",
            (f"access-token.{result_id}", result_id),
        )
    item(db)
    assert read_item_budget(db).spent_count == 1
    assert db.execute("SELECT count(DISTINCT secret_ref) FROM link_result").fetchone() == (2,)


@pytest.mark.parametrize("state", _RESULT_STATES)
def test_item_identity_counts_equal_once_distinct_twice(db: sqlite3.Connection, state: str) -> None:
    request(db)
    result(db, 1, item_id="synthetic-one", state=state)
    result(db, 2, item_id="synthetic-one", state=state)
    assert read_item_budget(db).spent_count == 1
    result(db, 3, item_id="synthetic-two", state=state)
    assert read_item_budget(db).spent_count == 2
    item(db, "synthetic-one")
    assert read_item_budget(db).spent_count == 2


@pytest.mark.parametrize("state", _RESULT_STATES)
def test_nameless_result_is_one_slot_except_ambiguous_exchanged(
    db: sqlite3.Connection,
    state: str,
) -> None:
    request(db)
    result(db, 1, state=state)
    assert read_item_budget(db).spent_count == 1
    item(db)
    if state == "EXCHANGED":
        with pytest.raises(ItemBudgetError, match="names no Item"):
            read_item_budget(db)
    else:
        assert read_item_budget(db).spent_count == 2


def test_digest_identity_is_request_scoped_and_duplicates_cannot_create_results(
    db: sqlite3.Connection,
) -> None:
    request(db)
    result(db, 1)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        db.execute(
            "INSERT INTO link_result(result_id, flow_id, token_digest, state) "
            "SELECT '00000000000000000000000000000002', flow_id, token_digest, state "
            "FROM link_result"
        )
    request(db, "request-b")
    db.execute(
        "INSERT INTO link_result(result_id, flow_id, token_digest, state) "
        "SELECT '00000000000000000000000000000002', 'request-b', token_digest, state "
        "FROM link_result"
    )
    assert read_item_budget(db).spent_count == 2


@pytest.mark.parametrize(
    "reason",
    [
        "MISSING_SESSION_ID",
        "MISSING_PUBLIC_TOKEN",
        "DIGEST_KEY_UNAVAILABLE",
        "LEGACY_ATTRIBUTION_AMBIGUOUS",
    ],
)
@pytest.mark.parametrize("state", _REQUEST_STATES)
@pytest.mark.parametrize("additional_slots", [0, 1, 2])
def test_zero_result_success_hold_refuses_until_explicit_accounting(
    db: sqlite3.Connection,
    reason: str,
    state: str,
    additional_slots: int,
) -> None:
    request(db, state=state)
    db.execute(
        "INSERT INTO link_success_observation(observation_id, flow_id, reason, observed_at) "
        "VALUES ('observation', 'request-a', ?, ?)",
        (reason, NOW),
    )
    for _ in range(2):
        with pytest.raises(ItemBudgetError, match="observation.*recorded adjudication"):
            read_item_budget(db)
    assert db.execute("SELECT count(*) FROM link_result").fetchone() == (0,)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        db.execute("UPDATE link_success_observation SET resolved_at = ?", (LATER,))
    db.execute(
        "UPDATE link_success_observation SET resolved_at = ?, "
        "resolution_note = ?, additional_slots = ?",
        (LATER, "Synthetic adjudication establishes additional lifetime slots", additional_slots),
    )
    budget = read_item_budget(db)
    assert budget.remaining == 10 - additional_slots
    assert len(budget.stranded) == additional_slots
    for slot in budget.stranded:
        assert slot.evidence is SlotEvidence.ADJUDICATED_OBSERVATION
        assert slot.state is None and slot.result_id is None
    db.execute(
        "INSERT INTO link_success_observation(observation_id, flow_id, reason, observed_at) "
        "VALUES ('another', 'request-a', ?, ?)",
        (reason, LATER),
    )
    with pytest.raises(ItemBudgetError, match="another"):
        read_item_budget(db)


def test_adjudicated_duplicate_adds_no_slot_and_keeps_audit(db: sqlite3.Connection) -> None:
    request(db)
    result(db, 1)
    db.execute(
        "INSERT INTO link_success_observation(observation_id, flow_id, link_session_id, "
        "reason, observed_at, resolved_at, resolution_note, additional_slots) VALUES "
        "('observation', 'request-a', NULL, 'DIGEST_KEY_UNAVAILABLE', ?, ?, ?, 0)",
        (NOW, LATER, "Synthetic adjudication: already counted by result one"),
    )
    assert read_item_budget(db).remaining == 9
    assert db.execute("SELECT reason, observed_at FROM link_success_observation").fetchone() == (
        "DIGEST_KEY_UNAVAILABLE",
        NOW,
    )
