"""`07b` criterion 3: the recovered Item's name reaches the row that bought it.

Every identifier here is synthetic. Real SQLite, real migrations and the real
`26a` reader — the point of most of these tests is not that an ``UPDATE`` ran but
that :func:`networth.item_budget.read_item_budget` stops counting one Link success
as two lifetime slots, which is the defect the criterion names.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from networth.item_budget import SlotEvidence, read_item_budget
from networth.link_pairing import (
    PairingBadUsage,
    PairingRefused,
    PairingResult,
    apply_pairing,
)
from networth.storage import migrate

FLOW = "1" * 32
RESULT = "2" * 32
SECOND = "3" * 32
STAMP = "2026-09-30T02:00:00Z"
ITEM = "synthetic-recovered-item-sentinel"
OTHER_ITEM = "synthetic-other-item-sentinel"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(tmp_path / "synthetic.sqlite")
    migrate(connection)
    connection.execute(
        "INSERT INTO link_request(flow_id, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, 'URL_MINTED')",
        (FLOW, STAMP, STAMP),
    )
    connection.commit()
    yield connection
    connection.close()


def result_row(
    connection: sqlite3.Connection,
    *,
    result_id: str = RESULT,
    state: str = "SUCCESS_PENDING_EXCHANGE",
    item_id: str | None = None,
    digest: str = "synthetic-digest",
) -> None:
    connection.execute(
        "INSERT INTO link_result(result_id, flow_id, token_digest, state, item_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (result_id, FLOW, digest, state, item_id),
    )
    connection.commit()


def legacy_row(connection: sqlite3.Connection, *, state: str = "SUCCESS_PENDING_EXCHANGE") -> None:
    """A flow whose evidence was never migrated into ``link_result``.

    The view's legacy arm is not dead code: `0006`'s comment keeps unmigrated
    rows visible precisely so an old flow still counts, and a recovery of one
    must reach the same destination.
    """
    cursor = connection.execute(
        "INSERT INTO link_flow(flow_id, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, ?)",
        (FLOW, STAMP, STAMP, state),
    )
    connection.execute(
        "UPDATE link_request SET legacy_link_flow_id = ? WHERE flow_id = ?",
        (cursor.lastrowid, FLOW),
    )
    connection.commit()


def stored_item(connection: sqlite3.Connection, plaid_item_id: str = ITEM) -> None:
    """The ``item`` row the restore commits beside the credential.

    Without this there is nothing for the nameless flow row to be double-counted
    *against*, and the test would pass with the bug present.
    """
    connection.execute(
        "INSERT INTO institution(plaid_institution_id, name, is_oauth) "
        "VALUES ('synthetic-institution', 'Synthetic institution', 0)"
    )
    connection.execute(
        "INSERT INTO item(institution_id, plaid_item_id, secret_ref, status, "
        "status_since, created_at) VALUES (1, ?, 'access_token:synthetic', 'HEALTHY', ?, ?)",
        (plaid_item_id, STAMP, STAMP),
    )
    connection.commit()


def names(connection: sqlite3.Connection) -> list[tuple[str | None, str | None]]:
    return [
        (row[0], row[1])
        for row in connection.execute(
            "SELECT result_id, item_id FROM link_success_evidence WHERE flow_id = ? "
            "ORDER BY result_id",
            (FLOW,),
        ).fetchall()
    ]


def test_the_nameless_row_is_what_double_counts_the_slot(db: sqlite3.Connection) -> None:
    """The defect, measured before the fix: one Link success, two slots spent.

    This is the assertion the criterion is written from — *"the same slot is
    counted **twice**, silently, and the owner is told he has one fewer lifetime
    Item than he does"*. It runs first so the rest of the file is read against a
    demonstrated cost rather than an assumed one.
    """
    result_row(db)
    stored_item(db)

    budget = read_item_budget(db)

    assert budget.spent_count == 2
    assert sorted(slot.evidence for slot in budget.spent) == [
        SlotEvidence.IN_FLIGHT_FLOW,
        SlotEvidence.ITEM,
    ]
    assert budget.remaining == budget.capacity - 2


def test_writing_the_pairing_collapses_the_two_slots_into_one(
    db: sqlite3.Connection,
) -> None:
    result_row(db)
    stored_item(db)

    outcome = apply_pairing(db, flow_id=FLOW, item_id=ITEM)

    assert outcome.result is PairingResult.WRITTEN
    assert outcome.paired
    assert names(db) == [(RESULT, ITEM)]
    budget = read_item_budget(db)
    assert budget.spent_count == 1
    assert [slot.evidence for slot in budget.spent] == [SlotEvidence.ITEM]


def test_the_write_is_committed_rather_than_left_in_a_transaction(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Read the pairing back through a *second* connection.

    The same connection would see its own uncommitted write, so it cannot tell a
    commit from an open transaction — and an emergency write-back that is still
    uncommitted when the process exits is the criterion unmet with a success
    message printed.
    """
    result_row(db)

    apply_pairing(db, flow_id=FLOW, item_id=ITEM)

    assert not db.in_transaction
    other = sqlite3.connect(tmp_path / "synthetic.sqlite")
    try:
        assert other.execute(
            "SELECT item_id FROM link_result WHERE result_id = ?", (RESULT,)
        ).fetchone() == (ITEM,)
    finally:
        other.close()


def test_an_unmigrated_legacy_flow_row_is_the_destination_for_its_own_recovery(
    db: sqlite3.Connection,
) -> None:
    legacy_row(db)
    stored_item(db)

    outcome = apply_pairing(db, flow_id=FLOW, item_id=ITEM)

    assert outcome.result is PairingResult.WRITTEN
    assert names(db) == [(None, ITEM)]
    assert db.execute("SELECT item_id FROM link_flow WHERE flow_id = ?", (FLOW,)).fetchone() == (
        ITEM,
    )
    assert read_item_budget(db).spent_count == 1


def test_a_second_restore_of_the_same_artifact_is_already_paired(
    db: sqlite3.Connection,
) -> None:
    result_row(db)
    assert apply_pairing(db, flow_id=FLOW, item_id=ITEM).result is PairingResult.WRITTEN

    outcome = apply_pairing(db, flow_id=FLOW, item_id=ITEM)

    assert outcome.result is PairingResult.ALREADY_PAIRED
    assert outcome.paired
    assert names(db) == [(RESULT, ITEM)]


def test_a_database_older_than_the_link_has_no_row_and_that_is_not_success(
    db: sqlite3.Connection,
) -> None:
    """The expected shape of this emergency: the backup predates the Link.

    ``NO_ROW`` must not read as paired. The pairing is still only in the
    artifact, and a caller that treated this as done would report a recovery
    complete while `26a` cannot see the slot at all.
    """
    outcome = apply_pairing(db, flow_id=FLOW, item_id=ITEM)

    assert outcome.result is PairingResult.NO_ROW
    assert not outcome.paired
    assert "only in the recovery artifact" in outcome.detail
    assert names(db) == []


def test_a_flow_unknown_to_this_database_is_also_no_row(db: sqlite3.Connection) -> None:
    outcome = apply_pairing(db, flow_id="4" * 32, item_id=ITEM)

    assert outcome.result is PairingResult.NO_ROW


def test_a_token_expired_row_is_written_and_the_disagreement_is_reported(
    db: sqlite3.Connection,
) -> None:
    """The VPS gave up on a token this recovery then exchanged successfully.

    Refusing here would preserve the double count in the one case where the slot
    is known for certain to have been spent, so the row is written and the
    contradiction is printed instead.
    """
    result_row(db, state="TOKEN_EXPIRED")
    stored_item(db)

    outcome = apply_pairing(db, flow_id=FLOW, item_id=ITEM)

    assert outcome.result is PairingResult.WRITTEN
    assert "TOKEN_EXPIRED" in outcome.detail
    assert "predates the recovery" in outcome.detail
    assert read_item_budget(db).spent_count == 1


def test_a_written_row_in_an_ordinary_state_does_not_claim_a_disagreement(
    db: sqlite3.Connection,
) -> None:
    result_row(db, state="EXCHANGING")

    assert "predates the recovery" not in apply_pairing(db, flow_id=FLOW, item_id=ITEM).detail


def test_a_committed_exchange_that_lost_its_item_name_is_refused(
    db: sqlite3.Connection,
) -> None:
    """``EXCHANGED`` + nameless means someone else's exchange was committed here."""
    result_row(db, state="EXCHANGED")

    with pytest.raises(PairingRefused, match="criterion 7"):
        apply_pairing(db, flow_id=FLOW, item_id=ITEM)

    assert names(db) == [(RESULT, None)]
    assert not db.in_transaction


def test_a_refusal_leaves_a_sibling_nameless_row_untouched(db: sqlite3.Connection) -> None:
    """The refusal must not be routed around by writing the other row instead.

    An ``EXCHANGED`` nameless row beside a writable one is the shape where a
    "pick any candidate" implementation looks correct: it would write the
    ``SUCCESS_PENDING_EXCHANGE`` row and report success, while the row that
    actually records a committed exchange stays nameless and `26a` keeps
    refusing the whole count.
    """
    result_row(db, state="EXCHANGED")
    result_row(db, result_id=SECOND, state="SUCCESS_PENDING_EXCHANGE", digest="second-digest")

    with pytest.raises(PairingRefused, match="criterion 7"):
        apply_pairing(db, flow_id=FLOW, item_id=ITEM)

    assert names(db) == [(RESULT, None), (SECOND, None)]


def test_rows_that_all_name_other_items_leave_this_recovery_nowhere_to_go(
    db: sqlite3.Connection,
) -> None:
    result_row(db, item_id=OTHER_ITEM)

    with pytest.raises(PairingRefused, match="already names a different Item"):
        apply_pairing(db, flow_id=FLOW, item_id=ITEM)

    assert names(db) == [(RESULT, OTHER_ITEM)]


def test_two_nameless_candidates_are_not_decidable_here(db: sqlite3.Connection) -> None:
    result_row(db)
    result_row(db, result_id=SECOND, state="EXCHANGING", digest="second-digest")

    with pytest.raises(PairingRefused, match="not decidable"):
        apply_pairing(db, flow_id=FLOW, item_id=ITEM)

    assert names(db) == [(RESULT, None), (SECOND, None)]


def test_a_sibling_already_naming_this_item_wins_over_a_nameless_one(
    db: sqlite3.Connection,
) -> None:
    """Idempotency is checked before ambiguity, and that order is the point.

    A re-run against a flow that also has an unrelated nameless row must report
    the write-back already done rather than refusing as undecidable — otherwise a
    restore that succeeded once can never be re-run to completion.
    """
    result_row(db, item_id=ITEM)
    result_row(db, result_id=SECOND, state="EXCHANGING", digest="second-digest")

    outcome = apply_pairing(db, flow_id=FLOW, item_id=ITEM)

    assert outcome.result is PairingResult.ALREADY_PAIRED
    assert names(db) == [(RESULT, ITEM), (SECOND, None)]


def test_an_open_transaction_is_refused_rather_than_joined(db: sqlite3.Connection) -> None:
    result_row(db)
    db.execute("BEGIN")

    with pytest.raises(PairingBadUsage, match="no active one"):
        apply_pairing(db, flow_id=FLOW, item_id=ITEM)


@pytest.mark.parametrize(
    ("flow_id", "item_id"),
    [("", ITEM), (FLOW, ""), ("", "")],
)
def test_an_empty_identifier_is_not_reported_as_a_missing_row(
    db: sqlite3.Connection, flow_id: str, item_id: str
) -> None:
    """``WHERE flow_id = ''`` matches nothing, which would read as ``NO_ROW``.

    That answer describes the database, and the fault is in the call — so the
    refusal has its own type rather than a measurement standing in for it.
    """
    result_row(db)

    with pytest.raises(PairingBadUsage, match="non-empty"):
        apply_pairing(db, flow_id=flow_id, item_id=item_id)


def test_neither_identifier_appears_in_any_message(db: sqlite3.Connection) -> None:
    """`AGENTS.md` rule 0: an Item id names one of the owner's institutions.

    Checked on the refusals because those are the messages that reach a
    transcript, and on the success detail for the same reason.
    """
    result_row(db, item_id=OTHER_ITEM)

    with pytest.raises(PairingRefused) as refusal:
        apply_pairing(db, flow_id=FLOW, item_id=ITEM)
    assert ITEM not in str(refusal.value)
    assert OTHER_ITEM not in str(refusal.value)

    db.execute("UPDATE link_result SET item_id = NULL WHERE result_id = ?", (RESULT,))
    db.commit()
    assert ITEM not in apply_pairing(db, flow_id=FLOW, item_id=ITEM).detail
