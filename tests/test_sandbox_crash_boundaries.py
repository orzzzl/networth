"""Task `06a` measurement (iii): the four crash boundaries (issues #5, #15).

What these record is not "the code works". It is **what a restarted worker can
conclude**, boundary by boundary — the input `07a`'s ``EXCHANGE_UNCERTAIN``
handling is written against. The headline result: everything that happens before
the success marker is durable is locally indistinguishable, so four distinct
local outcomes do not exist however carefully recovery is written.

That class grew by one in PR #58's review. The marker splits the third boundary
rather than deciding it, and the half in front of it — the response has arrived
and nothing has recorded it — is irreducible. The first version of this file
injected only the half behind the marker and reported "B3 is decidable", which
was true of what it measured and false of what it claimed.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from networth.tokenstore import TokenStore, new_flow_id
from tests.sandbox.crash_boundaries import (
    Boundary,
    Crashed,
    DuplicateExchangeNotMeasured,
    Observation,
    Outcome,
    PlaidLedger,
    observe,
    open_database,
    recover,
    run_exchange,
    seed,
)


@dataclass
class Rig:
    """One machine: a database, a token store, and Plaid's side of the wire."""

    database: Path
    store_dir: Path
    ledger: PlaidLedger
    flow_id: str

    def connect(self) -> sqlite3.Connection:
        """A *fresh* connection — the restart boundary, held nowhere in memory."""
        return open_database(self.database)

    def store(self) -> TokenStore:
        """A *fresh* store, for the same reason."""
        return TokenStore(self.store_dir)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    machine = Rig(
        database=tmp_path / "networth.db",
        store_dir=tmp_path / "secrets",
        ledger=PlaidLedger(),
        flow_id=new_flow_id(),
    )
    connection = machine.connect()
    seed(connection, machine.flow_id)
    connection.close()
    yield machine


def crash_at(rig: Rig, boundary: Boundary, *, request_arrived: bool = True) -> None:
    """Run one attempt to its injected failure, then drop the process's memory."""

    connection = rig.connect()
    try:
        with pytest.raises(Crashed):
            run_exchange(
                boundary=boundary,
                connection=connection,
                store=rig.store(),
                ledger=rig.ledger,
                flow_id=rig.flow_id,
                request_arrived=request_arrived,
            )
    finally:
        connection.close()


def recover_after(rig: Rig) -> tuple[Outcome, Observation, int]:
    """Restart and recover. Returns the outcome, the view, and Plaid's call count."""

    connection = rig.connect()
    try:
        store = rig.store()
        outcome = recover(connection, store, rig.flow_id)
        return outcome, observe(connection, store, rig.flow_id), rig.ledger.calls
    finally:
        connection.close()


# --- the control: the happy path, so a green boundary test means something ----


def test_no_crash_completes_and_spends_exactly_one_token(rig: Rig) -> None:
    connection = rig.connect()
    run_exchange(
        boundary=Boundary.NO_CRASH,
        connection=connection,
        store=rig.store(),
        ledger=rig.ledger,
        flow_id=rig.flow_id,
    )
    seen = observe(connection, rig.store(), rig.flow_id)
    connection.close()

    assert seen.flow_state == "EXCHANGED"
    assert seen.material_durable is True
    assert seen.item_row_exists is True
    assert rig.ledger.calls == 1
    assert rig.ledger.consumed is True


# --- boundary 4: the one rev 18 got wrong -------------------------------------


def test_after_fsync_completes_locally_without_a_second_exchange(rig: Rig) -> None:
    """The credential is already durable, so recovery finishes the local write.

    Classifying this ``EXCHANGE_UNCERTAIN`` would report a lost lifetime slot
    that was never lost. The assertion that carries the finding is
    ``calls == 1``: recovery reached a terminal, correct state **without
    touching Plaid**.
    """

    crash_at(rig, Boundary.AFTER_FSYNC_BEFORE_COMMIT)
    outcome, seen, calls = recover_after(rig)

    assert outcome is Outcome.COMPLETED_LOCALLY
    assert calls == 1, "recovery must not exchange a second time"
    assert seen.flow_state == "EXCHANGED"
    assert seen.item_row_exists is True
    assert seen.material_durable is True


def test_after_fsync_is_recoverable_even_though_the_db_saw_nothing(rig: Rig) -> None:
    """Before recovery the database has no idea the credential exists.

    The ``item`` row is absent and ``link_flow.secret_ref`` is NULL — the whole
    reason ``reconcile`` is keyed on ``flow_id`` and not on a database column.
    """

    crash_at(rig, Boundary.AFTER_FSYNC_BEFORE_COMMIT)
    connection = rig.connect()
    seen = observe(connection, rig.store(), rig.flow_id)
    connection.close()

    assert seen.material_durable is True
    assert seen.secret_ref_in_db is None
    assert seen.item_row_exists is False
    assert seen.flow_state == "EXCHANGING"


# --- boundaries 1–3: what recovery can and cannot decide ----------------------


@pytest.mark.parametrize(
    ("boundary", "request_arrived", "consumed"),
    [
        (Boundary.BEFORE_SEND, True, False),
        (Boundary.AFTER_SEND_BEFORE_RESPONSE, False, False),
        (Boundary.AFTER_SEND_BEFORE_RESPONSE, True, True),
        (Boundary.AFTER_RESPONSE_BEFORE_MARKER, True, True),
    ],
)
def test_before_the_marker_nothing_can_be_decided_locally(
    rig: Rig, boundary: Boundary, request_arrived: bool, consumed: bool
) -> None:
    """Every crash before the success marker leaves nothing that separates it.

    The ground truth differs across these rows — the token is untouched in two
    of them and consumed in the other two — and recovery reaches the same
    conclusion in all four, because that is genuinely all the disk supports.
    The last row is B3a and was added in PR #58's review: it is the reason the
    class is "before the marker" rather than "boundaries one and two".
    """

    crash_at(rig, boundary, request_arrived=request_arrived)
    outcome, seen, _ = recover_after(rig)

    assert outcome is Outcome.NEEDS_PLAID_ADJUDICATION
    assert seen.material_durable is False
    assert seen.request_id_recorded is None
    assert rig.ledger.consumed is consumed, "the rig's own ground truth"


def test_after_the_marker_is_known_stranded_not_merely_uncertain(rig: Rig) -> None:
    """Boundary three's **second half** is decidable, and the report is stronger.

    A recorded ``request_id`` means the exchange returned material, so the token
    is certainly consumed and the credential is certainly gone. Calling that
    ``EXCHANGE_UNCERTAIN`` — "it is not known whether Plaid consumed the token"
    (§7) — understates what is known. `07a` has no state for this, and per PR
    #58's review it is not getting one until the other half closes too.
    """

    crash_at(rig, Boundary.AFTER_MARKER_BEFORE_FSYNC)
    outcome, seen, _ = recover_after(rig)

    assert outcome is Outcome.STRANDED_KNOWN
    assert seen.material_durable is False
    assert seen.request_id_recorded is not None
    assert rig.ledger.consumed is True


def test_the_pre_marker_half_of_boundary_three_is_irreducible(rig: Rig) -> None:
    """**The correction.** The marker moves the undecidable window; it does not
    close it.

    Injected after ``ledger.exchange()`` returns and before the marker write: the
    token is consumed, and the disk says exactly what it says after a crash
    *before the send*. So the earlier claim that "B3 is decidable because of the
    marker" was drawn from B3b alone. This half stays in the shared-uncertainty
    class and no local write can rescue it — every candidate barrier sits either
    before the response (too early to know) or after this point (too late to be
    reached).

    Not repaired by adding a second durability barrier ahead of
    :meth:`TokenStore.put`: that would shrink this window only by growing the one
    in which the credential itself is lost, which is the worse trade.
    """

    crash_at(rig, Boundary.AFTER_RESPONSE_BEFORE_MARKER)
    outcome, seen, _ = recover_after(rig)

    assert outcome is Outcome.NEEDS_PLAID_ADJUDICATION
    assert seen.request_id_recorded is None
    assert seen.material_durable is False
    assert rig.ledger.consumed is True, "the rig's ground truth: the slot is spent"


def test_the_two_halves_of_boundary_three_are_not_the_same_boundary() -> None:
    """One crash point is decidable and the other is not, in the same worker.

    Asserted as a pair because the finding is a *difference*: a rig that only
    ever injected at B3b could report "B3 is decidable" forever and never be
    wrong about anything it measured.
    """

    with _fresh_machine() as machine:
        crash_at(machine, Boundary.AFTER_MARKER_BEFORE_FSYNC)
        after_marker = recover_after(machine)

    with _fresh_machine() as machine:
        crash_at(machine, Boundary.AFTER_RESPONSE_BEFORE_MARKER)
        before_marker = recover_after(machine)

    assert after_marker[0] is Outcome.STRANDED_KNOWN
    assert before_marker[0] is Outcome.NEEDS_PLAID_ADJUDICATION
    assert after_marker[1] != before_marker[1], "the marker is the whole difference"


def test_recovery_never_reads_the_ledger(rig: Rig) -> None:
    """The separation the measurement rests on, asserted rather than described.

    ``recover`` takes a connection and a store. If it could see the ledger, every
    outcome above would be a statement about this rig instead of about a real
    restarted worker.
    """

    import inspect

    from tests.sandbox import crash_boundaries

    parameters = set(inspect.signature(crash_boundaries.recover).parameters)
    assert parameters == {"connection", "store", "flow_id"}


# --- the headline: everything before the marker, one observable state ---------


def test_every_crash_before_the_marker_leaves_byte_identical_evidence() -> None:
    """**The measurement.** Four different worlds, one observable state.

    `tasks/README.md` asks each of the four boundaries to produce "a distinct,
    honest outcome". Run against the real schema and the real `TokenStore`, three
    of them do not: nothing on disk separates "about to send" from "sent, no
    reply" from "replied, not yet written down", and the token is *untouched* in
    two of these runs and *consumed* in the other two. Reporting the first as
    ``EXCHANGE_UNCERTAIN`` claims a slot is at risk when it provably is not —
    rev 18's error pointing the other way — and no amount of care in `07a` can
    avoid it, because the evidence is not there.

    The fourth row is B3a and it is why this test's name changed in PR #58's
    review: with only the first three, the same green result would have
    supported the stronger and **false** claim that the marker makes all of B3
    decidable.

    Which is exactly why measurement (ii) gates `07a`: the re-exchange is the
    only oracle that can split this class.
    """

    seen: list[Observation] = []
    ground_truth: list[bool] = []

    for boundary, arrived in (
        (Boundary.BEFORE_SEND, True),
        (Boundary.AFTER_SEND_BEFORE_RESPONSE, False),
        (Boundary.AFTER_SEND_BEFORE_RESPONSE, True),
        (Boundary.AFTER_RESPONSE_BEFORE_MARKER, True),
    ):
        with _fresh_machine() as machine:
            crash_at(machine, boundary, request_arrived=arrived)
            connection = machine.connect()
            seen.append(observe(connection, machine.store(), machine.flow_id))
            connection.close()
            ground_truth.append(machine.ledger.consumed)

    assert len(set(seen)) == 1, f"expected one observable state, got {set(seen)}"
    assert set(ground_truth) == {False, True}, (
        "the runs must not share a ground truth, or the test proves nothing"
    )


def test_boundary_three_collapses_entirely_without_the_request_id_write() -> None:
    """And B3b is only decidable because of one durable write.

    This is the actionable half of (iii), and it asks a different question from
    :func:`test_the_pre_marker_half_of_boundary_three_is_irreducible`. That one
    varies *where this worker dies*; this one varies *the worker*: drop the
    marker from the design and a crash at B3b — the one place in B3 the marker
    would have helped — becomes byte-identical to B1/B2 as well, so a
    certainly-lost slot gets reported as merely uncertain.

    So `07a` must durably record a *successful* exchange response **before**
    writing the credential. That is a real cost — an extra barrier in the hot
    path — and it buys *part* of B3, not all of it. Stated here as a measured
    consequence rather than a preference.
    """

    with _fresh_machine() as machine:
        crash_at(machine, Boundary.AFTER_MARKER_BEFORE_FSYNC)
        with_write = recover_after(machine)[1]

    with _fresh_machine() as machine:
        connection = machine.connect()
        with pytest.raises(Crashed):
            run_exchange(
                boundary=Boundary.AFTER_MARKER_BEFORE_FSYNC,
                connection=connection,
                store=machine.store(),
                ledger=machine.ledger,
                flow_id=machine.flow_id,
                persist_request_id=False,
            )
        connection.close()
        outcome, without_write, _ = recover_after(machine)

    assert with_write != without_write, "the write is what makes B3b decidable"
    assert with_write.request_id_recorded is not None
    assert without_write.request_id_recorded is None
    assert outcome is Outcome.NEEDS_PLAID_ADJUDICATION, (
        "without the write, a certainly-stranded slot is only 'uncertain'"
    )


def test_the_rig_refuses_to_invent_a_duplicate_exchange_response(rig: Rig) -> None:
    """Measurement (ii) is live-only, so the rig will not answer for it.

    A fixture that returned some plausible ``INVALID_PUBLIC_TOKEN`` here would
    make every adjudication test above pass on a guess about the one Plaid
    behaviour `07a` is written against.
    """

    rig.ledger.exchange("public-token-rig")
    with pytest.raises(DuplicateExchangeNotMeasured):
        rig.ledger.exchange("public-token-rig")


# --- helper -------------------------------------------------------------------


class _fresh_machine:
    """A whole new machine per boundary, so no run can see another's disk."""

    def __init__(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()

    def __enter__(self) -> Rig:
        root = Path(self._tmp.name)
        machine = Rig(
            database=root / "networth.db",
            store_dir=root / "secrets",
            ledger=PlaidLedger(),
            flow_id=new_flow_id(),
        )
        connection = machine.connect()
        seed(connection, machine.flow_id)
        connection.close()
        return machine

    def __exit__(self, *exc: object) -> None:
        self._tmp.cleanup()
