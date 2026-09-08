"""Task 26a: the remaining-slot count, and the counting rules behind it.

Every test runs against a fixture database. Nothing here creates, needs, or can
acquire a Production Item — the point of the task is to make the count provable
without spending the resource it counts.
"""

from __future__ import annotations

import io
import re
import sqlite3
from collections.abc import Iterator
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta

import pytest

from networth.item_budget import (
    _CLASSIFIED_STATES,
    LIFETIME_ITEM_SLOTS,
    ItemBudget,
    ItemBudgetError,
    SlotEvidence,
    SpentSlot,
    read_item_budget,
)
from networth.storage import migrate

NOW = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)

#: Section 7's ten states, split by the table's own "Slot spent?" column.
SPENDS_NOTHING = ("URL_MINTED", "SESSION_STARTED", "SESSION_EXITED", "URL_EXPIRED", "ABANDONED")
STRANDS_A_SLOT = ("TOKEN_EXPIRED", "EXCHANGE_UNCERTAIN")
SPENDS_WHILE_UNRESOLVED = ("SUCCESS_PENDING_EXCHANGE", "EXCHANGING")


def _db_time(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    connection.execute(
        "INSERT INTO institution(plaid_institution_id, name, is_oauth) "
        "VALUES ('synthetic-institution', 'Synthetic institution', 0)"
    )
    try:
        yield connection
    finally:
        connection.close()


def add_item(
    connection: sqlite3.Connection,
    suffix: str,
    *,
    replaces_item_id: int | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO item(
            institution_id, plaid_item_id, secret_ref, status, status_since,
            replaces_item_id, created_at
        ) VALUES (1, ?, ?, 'HEALTHY', ?, ?, ?)
        """,
        (
            f"plaid-item-{suffix}",
            f"secret-ref-{suffix}",
            _db_time(NOW),
            replaces_item_id,
            _db_time(NOW),
        ),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def add_flow(
    connection: sqlite3.Connection,
    suffix: str,
    state: str,
    *,
    item_id: str | None = None,
    url_expires_at: datetime = NOW + timedelta(hours=4),
) -> None:
    connection.execute(
        """
        INSERT INTO link_flow(flow_id, minted_at, hosted_url_expires_at, state, item_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (f"flow-{suffix}", _db_time(NOW), _db_time(url_expires_at), state, item_id),
    )


def test_an_empty_database_has_every_lifetime_slot(db: sqlite3.Connection) -> None:
    budget = read_item_budget(db)

    assert budget.capacity == LIFETIME_ITEM_SLOTS == 10
    assert budget.remaining == 10
    assert budget.spent == ()


def test_each_stored_item_costs_one_slot(db: sqlite3.Connection) -> None:
    for suffix in ("a", "b", "c"):
        add_item(db, suffix)

    budget = read_item_budget(db)

    assert budget.spent_count == 3
    assert budget.remaining == 7
    assert {slot.plaid_item_id for slot in budget.usable} == {
        "plaid-item-a",
        "plaid-item-b",
        "plaid-item-c",
    }


# --- Issue #7's two acceptance tests -------------------------------------------------


@pytest.mark.parametrize("state", SPENDS_NOTHING)
def test_a_link_that_never_succeeded_spends_no_slot(db: sqlite3.Connection, state: str) -> None:
    """**F2a**: the slot is spent when Link succeeds, so these cost nothing."""

    add_flow(db, "unspent", state)

    budget = read_item_budget(db)

    assert budget.spent == ()
    assert budget.remaining == 10


def test_a_url_minted_row_whose_url_expired_is_not_a_stranded_slot(
    db: sqlite3.Connection,
) -> None:
    """Issue #7 acceptance 1, and the reason spentness is read from the state.

    The deadline is long past in wall-clock terms. A count that compared
    ``hosted_url_expires_at`` against the clock would report a burned slot for a
    URL nobody ever opened — the pessimistic error issue #7 names, which stops
    the owner linking accounts he could still link.
    """

    add_flow(db, "never-opened", "URL_MINTED", url_expires_at=NOW - timedelta(days=30))

    budget = read_item_budget(db)

    assert budget.stranded == ()
    assert budget.remaining == 10


@pytest.mark.parametrize("state", STRANDS_A_SLOT)
def test_token_expired_and_exchange_uncertain_are_counted(
    db: sqlite3.Connection, state: str
) -> None:
    """Issue #7 acceptance 2: both follow a completed Link, so both cost."""

    add_flow(db, "stranded", state)

    budget = read_item_budget(db)

    assert budget.remaining == 9
    (slot,) = budget.stranded
    assert slot.evidence is SlotEvidence.STRANDED_FLOW
    assert slot.state == state
    assert slot.flow_id == "flow-stranded"


def test_a_stranded_flow_is_distinguishable_from_a_usable_item(db: sqlite3.Connection) -> None:
    """The count alone cannot be acted on; the caller must be able to explain it."""

    add_item(db, "usable")
    add_flow(db, "gone", "TOKEN_EXPIRED")

    budget = read_item_budget(db)

    assert budget.remaining == 8
    assert len(budget.usable) == 1
    assert len(budget.stranded) == 1


# --- In-flight: spent, but not yet stranded ------------------------------------------


@pytest.mark.parametrize("state", SPENDS_WHILE_UNRESOLVED)
def test_a_succeeded_link_costs_its_slot_before_the_exchange_resolves(
    db: sqlite3.Connection, state: str
) -> None:
    """Section 7's table marks both states "slot spent: yes".

    Leaving them out would make the count optimistic for the ~30 minutes
    between Link success and ``token_exchange_expires_at`` — the direction
    issue #7 describes as running out without warning, and precisely the window
    in which task 08 asks the owner to confirm another Link.
    """

    add_flow(db, "pending", state)

    budget = read_item_budget(db)

    assert budget.remaining == 9
    (slot,) = budget.in_flight
    assert slot.evidence is SlotEvidence.IN_FLIGHT_FLOW
    assert slot.state == state
    assert budget.stranded == ()


def test_in_flight_and_stranded_are_reported_apart(db: sqlite3.Connection) -> None:
    """Same cost, different outcomes: one may still become a usable Item."""

    add_flow(db, "pending", "SUCCESS_PENDING_EXCHANGE")
    add_flow(db, "stranded", "EXCHANGE_UNCERTAIN")

    budget = read_item_budget(db)

    assert budget.remaining == 8
    assert len(budget.in_flight) == 1
    assert len(budget.stranded) == 1


# --- Counting one slot once (acceptance criterion 4) ---------------------------------


def test_an_exchanged_flow_and_its_item_are_one_slot(db: sqlite3.Connection) -> None:
    add_item(db, "linked")
    add_flow(db, "linked", "EXCHANGED", item_id="plaid-item-linked")

    budget = read_item_budget(db)

    assert budget.remaining == 9
    assert budget.spent_count == 1
    assert budget.spent[0].evidence is SlotEvidence.ITEM


def test_an_item_recovered_by_07b_costs_one_slot_not_two(db: sqlite3.Connection) -> None:
    """Acceptance criterion 4, and it does not depend on which host recovered it.

    Task 07b exchanges from a second host when the VPS is unavailable, so the
    ``link_flow`` row can still read ``EXCHANGE_UNCERTAIN`` while the ``item``
    row exists. Identity, not the flow state, decides that this is one slot.
    """

    add_item(db, "recovered")
    add_flow(db, "recovered", "EXCHANGE_UNCERTAIN", item_id="plaid-item-recovered")

    budget = read_item_budget(db)

    assert budget.remaining == 9
    assert budget.spent_count == 1
    assert budget.stranded == ()
    assert budget.usable[0].plaid_item_id == "plaid-item-recovered"


@pytest.mark.parametrize("order", [("uncertain", "exchanged"), ("exchanged", "uncertain")])
def test_two_flow_rows_naming_one_item_read_the_same_in_either_order(
    db: sqlite3.Connection, order: tuple[str, str]
) -> None:
    """A retried exchange writes a second row; the Item behind it is still one.

    And *which* row was written first is not a fact about the Item. Classifying
    the earliest row and stopping reported this same pair as ``STRANDED_FLOW``
    or ``ORPHANED_FLOW`` purely by insertion order, so a caller explaining the
    number could be told two different things about one database.
    """

    states = {"uncertain": "EXCHANGE_UNCERTAIN", "exchanged": "EXCHANGED"}
    for suffix in order:
        add_flow(db, suffix, states[suffix], item_id="plaid-item-retried")

    budget = read_item_budget(db)

    assert budget.remaining == 9
    assert budget.spent_count == 1
    (slot,) = budget.orphaned
    assert slot.state == "EXCHANGED"
    assert slot.flow_id == "flow-exchanged"
    assert budget.stranded == ()
    assert budget.in_flight == ()


@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
def test_duplicate_rows_of_one_state_always_name_the_same_flow(
    db: sqlite3.Connection, order: tuple[str, str]
) -> None:
    """The tie-break is the flow's own name, not the order the rows arrived in.

    Two rows of the same state agree about the label, so only the *named* flow
    could still move; pinning it keeps the whole answer a function of the data.
    """

    for suffix in order:
        add_flow(db, suffix, "EXCHANGE_UNCERTAIN", item_id="plaid-item-retried")

    budget = read_item_budget(db)

    assert budget.spent_count == 1
    (slot,) = budget.stranded
    assert slot.flow_id == "flow-a"


def test_flows_without_an_item_id_each_cost_their_own_slot(db: sqlite3.Connection) -> None:
    """``item_id`` is set on exchange, so a stranded flow usually has none.

    Two such rows are two separate Link successes. Collapsing them because they
    share a NULL would under-report a permanently spent resource.
    """

    add_flow(db, "one", "TOKEN_EXPIRED")
    add_flow(db, "two", "TOKEN_EXPIRED")

    budget = read_item_budget(db)

    assert budget.remaining == 8
    assert budget.spent_count == 2


def test_an_exchanged_flow_without_its_item_row_costs_and_says_it_is_wrong(
    db: sqlite3.Connection,
) -> None:
    """The slot is spent whether or not the row that proves it was committed.

    But section 7 enters ``EXCHANGED`` only *after* committing that row, so its
    absence is a contradiction rather than an exchange still in progress.
    Calling it ``IN_FLIGHT_FLOW`` gave task 26 a diagnosis — "wait, it may still
    resolve" — that the state machine says can never come true.
    """

    add_flow(db, "orphan", "EXCHANGED", item_id="plaid-item-orphan")

    budget = read_item_budget(db)

    assert budget.remaining == 9
    assert budget.in_flight == ()
    (slot,) = budget.orphaned
    assert slot.plaid_item_id == "plaid-item-orphan"
    assert slot.state == "EXCHANGED"


def test_a_nameless_exchanged_flow_beside_a_stored_item_has_no_count_to_return(
    db: sqlite3.Connection,
) -> None:
    """The same contradiction reached the other way, and it costs the *number*.

    ``item_id`` is written by the exchange that also writes the ``item`` row, so
    an ``EXCHANGED`` row without one names nothing to reconcile against: it may
    be the flow that bought the Item stored beside it, or a second slot whose
    row was never written. One database, two possible counts.

    An ``orphaned`` annotation cannot repair that. The caller is still handed a
    definite ``remaining`` that may be one too low, and issue #7 names that
    direction as the harmful one — it stops a Link the owner could still make.
    So this is the case that refuses, as a broken replacement chain does.
    """

    add_item(db, "linked")
    add_flow(db, "nameless", "EXCHANGED")

    # Both halves: the right row, and refused for the ambiguity rather than for
    # some other complaint that happens to name it.
    with pytest.raises(ItemBudgetError, match=r"flow-nameless.*names no Item"):
        read_item_budget(db)


def test_a_nameless_exchanged_flow_with_no_item_to_confuse_it_still_costs_one(
    db: sqlite3.Connection,
) -> None:
    """And the refusal is exactly as wide as the ambiguity, not wider.

    With no ``item`` row stored, the same row cannot be describing one: Link
    succeeded, so the slot is spent (**F2a**), and one is the only count this
    evidence admits. Refusing here as well would withhold a number that *is*
    known — from task 08's point of view the owner is blocked either way, so a
    refusal has to be paid for by a real ambiguity.
    """

    add_flow(db, "nameless", "EXCHANGED")

    budget = read_item_budget(db)

    assert budget.remaining == 9
    (slot,) = budget.orphaned
    assert slot.plaid_item_id is None
    assert slot.flow_id == "flow-nameless"


# --- Replacements (acceptance criterion 3) -------------------------------------------


def test_a_replacement_costs_a_second_slot_and_says_which_it_replaced(
    db: sqlite3.Connection,
) -> None:
    """**F2**: the replaced Item's slot was never returned, so both are spent."""

    original = add_item(db, "original")
    add_item(db, "replacement", replaces_item_id=original)

    budget = read_item_budget(db)

    assert budget.remaining == 8
    assert budget.spent_count == 2
    (replacement,) = budget.replacements
    assert replacement.plaid_item_id == "plaid-item-replacement"
    assert replacement.replaces_plaid_item_id == "plaid-item-original"


def test_an_item_that_replaced_nothing_reports_no_chain(db: sqlite3.Connection) -> None:
    add_item(db, "solo")

    assert read_item_budget(db).replacements == ()


def test_a_replacement_chain_that_lost_a_link_is_refused(db: sqlite3.Connection) -> None:
    """The foreign key that forbids this is per connection and defaults off.

    A row can therefore outlive the guarantee that wrote it. Silently reporting
    ``replaces_plaid_item_id=None`` would understate where the slots went, so
    the read refuses rather than answers.
    """

    original = add_item(db, "original")
    add_item(db, "replacement", replaces_item_id=original)
    # `PRAGMA foreign_keys` is a silent no-op inside a transaction, so the
    # inserts above have to be committed before the setting can be turned off.
    # That is the same property that makes the pragma something a reader must
    # assert rather than set, and it is why this row is reachable at all.
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("DELETE FROM item WHERE id = ?", (original,))

    with pytest.raises(ItemBudgetError, match="replaces a row that no longer exists"):
        read_item_budget(db)


# --- The whole picture ----------------------------------------------------------------


def test_a_mixed_database_counts_every_slot_exactly_once(db: sqlite3.Connection) -> None:
    original = add_item(db, "original")
    add_item(db, "replacement", replaces_item_id=original)
    add_item(db, "recovered")
    add_flow(db, "recovered", "EXCHANGE_UNCERTAIN", item_id="plaid-item-recovered")
    add_flow(db, "stranded", "TOKEN_EXPIRED")
    add_flow(db, "pending", "SUCCESS_PENDING_EXCHANGE")
    for state in SPENDS_NOTHING:
        add_flow(db, f"free-{state}", state)

    budget = read_item_budget(db)

    assert budget.spent_count == 5
    assert budget.remaining == 5
    assert len(budget.usable) == 3
    assert len(budget.stranded) == 1
    assert len(budget.in_flight) == 1
    assert len(budget.replacements) == 1
    # Nothing ordinary is read as a contradiction: every row above is one
    # section 7 can produce, including the recovered Item's stale flow state.
    assert budget.orphaned == ()


def test_the_count_is_the_length_of_its_own_evidence(db: sqlite3.Connection) -> None:
    """No number without its provenance: the two can never disagree."""

    add_item(db, "a")
    add_flow(db, "b", "TOKEN_EXPIRED")
    add_flow(db, "c", "EXCHANGING")
    add_flow(db, "d", "EXCHANGED", item_id="plaid-item-missing")

    budget = read_item_budget(db)

    assert budget.remaining == budget.capacity - len(budget.spent)
    assert budget.spent_count == len(
        budget.usable + budget.stranded + budget.in_flight + budget.orphaned
    )


def test_reading_the_budget_prints_nothing(db: sqlite3.Connection) -> None:
    """Surfacing is task 26. This module returns facts and says nothing."""

    add_item(db, "a")
    add_flow(db, "b", "TOKEN_EXPIRED")

    stream = io.StringIO()
    with redirect_stdout(stream):
        read_item_budget(db)

    assert stream.getvalue() == ""


def test_the_read_writes_nothing(db: sqlite3.Connection) -> None:
    """A count that mutated the thing it counts would be its own second source."""

    add_item(db, "a")
    add_flow(db, "b", "EXCHANGE_UNCERTAIN")
    before = (
        db.execute("SELECT * FROM item").fetchall(),
        db.execute("SELECT * FROM link_flow").fetchall(),
    )

    read_item_budget(db)

    assert before == (
        db.execute("SELECT * FROM item").fetchall(),
        db.execute("SELECT * FROM link_flow").fetchall(),
    )


def _link_flow_ddl(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'link_flow'"
    ).fetchone()
    assert row is not None, "the migration no longer creates link_flow"
    return str(row[0])


def _states_the_schema_admits(ddl: str) -> set[str]:
    """Every literal in ``link_flow``'s ``state IN (...)`` CHECK, read whole.

    Searching the DDL for the names this file already knows can only confirm
    what it already believes: an eleventh state would leave every assertion
    below green while the production query silently dropped it, which is the
    exact failure the guard exists to stop.
    """

    # `\b` and not just `state`: a later column named `..._state` with its own
    # IN list would otherwise be matched first, and this guard would then check
    # the wrong constraint while still looking like it passed.
    match = re.search(r"\bstate\s+IN\s*\(([^)]*)\)", ddl)
    assert match is not None, "link_flow no longer constrains `state` with an IN list"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_the_schema_and_this_module_classify_the_same_states(db: sqlite3.Connection) -> None:
    """The schema's CHECK is the authority on how many states exist.

    A state added to the schema without a decision here would otherwise default
    to costing nothing, which is the optimistic direction. Both comparisons run
    in both directions, and the second one is against the *module's* own
    classification rather than this file's — a test that shares its expectation
    with nothing but itself cannot see production fall behind the schema.
    """

    admitted = _states_the_schema_admits(_link_flow_ddl(db))
    documented = set(SPENDS_NOTHING) | set(STRANDS_A_SLOT) | set(SPENDS_WHILE_UNRESOLVED)
    documented.add("EXCHANGED")

    assert admitted == documented
    assert admitted == set(_CLASSIFIED_STATES)
    assert len(admitted) == 10


def test_an_unclassified_state_added_to_the_schema_is_caught(db: sqlite3.Connection) -> None:
    """The guard above must be able to go red, so make it.

    A green check that cannot fail is not evidence. This builds the eleventh
    state the guard is meant to catch, from the real DDL, and asserts the
    mutation applied before asserting the consequence — a renamed state would
    otherwise turn the mutation into a no-op and pass this vacuously.
    """

    ddl = _link_flow_ddl(db)
    mutated = ddl.replace("'ABANDONED'", "'ABANDONED',\n            'INVENTED_STATE'", 1)
    assert mutated != ddl, "the mutation did not apply; this test would prove nothing"

    scratch = sqlite3.connect(":memory:")
    try:
        scratch.execute(mutated)
        admitted = _states_the_schema_admits(_link_flow_ddl(scratch))
    finally:
        scratch.close()

    assert admitted - set(_CLASSIFIED_STATES) == {"INVENTED_STATE"}
    assert admitted != set(_CLASSIFIED_STATES)


# --- Record invariants ----------------------------------------------------------------


def test_a_spent_slot_cannot_claim_item_evidence_without_an_item(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="ITEM evidence requires a plaid_item_id"):
        SpentSlot(
            evidence=SlotEvidence.ITEM,
            plaid_item_id=None,
            flow_id=None,
            state=None,
            replaces_plaid_item_id=None,
        )


def test_flow_evidence_must_name_its_flow(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="flow evidence requires a flow_id"):
        SpentSlot(
            evidence=SlotEvidence.STRANDED_FLOW,
            plaid_item_id=None,
            flow_id=None,
            state="TOKEN_EXPIRED",
            replaces_plaid_item_id=None,
        )


def test_a_negative_remaining_is_reported_rather_than_rounded_away(
    db: sqlite3.Connection,
) -> None:
    """Plaid stops past ten, so this means the database disagrees with F2."""

    spent = tuple(
        SpentSlot(
            evidence=SlotEvidence.ITEM,
            plaid_item_id=f"plaid-item-{n}",
            flow_id=None,
            state=None,
            replaces_plaid_item_id=None,
        )
        for n in range(12)
    )

    assert ItemBudget(capacity=LIFETIME_ITEM_SLOTS, spent=spent).remaining == -2


def test_a_connection_is_required(db: sqlite3.Connection) -> None:
    with pytest.raises(TypeError, match="sqlite3.Connection"):
        read_item_budget("not a connection")  # type: ignore[arg-type]
