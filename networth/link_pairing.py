"""The recovered ``item_id``, written back onto the flow row that bought the Item.

`07b` criterion 3, and it is an **accounting** criterion rather than a credential
one. By the time this module runs the credential is already durable — criteria 1
and 2 own that — and what is still missing is the *name* of the Item on the row
that recorded the Link success:

    **The recovered ``item_id`` is written back onto the originating ``link_flow``
    row, and recovery is not reported successful until it is.** `26a` (#54)
    reconciles the ``item`` and ``link_flow`` tables **by Item identity**; a
    recovery that stores the credential and leaves its flow row nameless is
    indistinguishable from an ordinary stranded flow — nameless is the *expected*
    shape for one — so the same slot is counted **twice**, silently, and the owner
    is told he has one fewer lifetime Item than he does.

The line that makes it concrete is in :mod:`networth.item_budget`, which reads the
``link_success_evidence`` view: a success row whose ``item_id`` is ``NULL`` becomes
*"its own slot: two rows sharing a NULL are two separate Link successes"*. So the
nameless row counts once and the ``item`` row the restore commits counts again —
two of ten lifetime slots for one Link — and nothing inside that read can tell
they are the same one. **F2a** makes the error permanent: slots do not come back.

**Why the write-back lives here and not in `26a`.** The criterion says it: *"`26a`
cannot detect either case from inside itself"*. A nameless success row is a valid
shape — an ordinary stranded flow is exactly that — so the reconciler has no
predicate that separates "stranded" from "recovered but never named". The
knowledge that these two rows are one slot exists in exactly one place, the
process that just exchanged the token, and it is only there for as long as that
process runs. Writing it down is this module's whole job.

**Two destinations, and this module is the second half of both.** Criterion 3:

    The write-back has two destinations and the script must say which one it used:
    onto the restored ``link_flow`` copy when recovery lands on a replacement host,
    or carried inside the Mac-side emergency artifact and applied during restore
    when the originating row is simply gone with the VPS.

:class:`~networth.link_sink.Pairing` is the part that *says which*;
:func:`apply_pairing` is the part that applies it. The artifact branch reaches here
through :func:`networth.link_sink.restore`, which opens the sealed pairing and
hands it over — the replacement host's database is the only place a row can be
written, and on the Mac there is no such row to write.

**Refusing is a real outcome here, and more of the branches refuse than write.**
The one shape this module may write is *"a success row for this flow exists and
names no Item"*. Everything else — a row already naming a different Item, a row
that reached ``EXCHANGED`` without a name, two nameless candidates — is a
disagreement between what this recovery believes and what the database records,
and the repair for each is a different one. Picking a row to write in those cases
would put this Item's name on a flow that did not buy it, which is **worse than
nameless**: `26a` would then reconcile a wrong pair *and* still leave the right
row unnamed, so a fabricated fact would hide the real one. Nothing here is
inferred from a count.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Final

#: The states ``link_success_evidence`` projects, i.e. the rows that mean a Link
#: success has been observed. Named for the docstrings below rather than used as a
#: filter: the view has already applied it, and re-stating it as a ``WHERE`` clause
#: here would be a second copy of `07a`'s state machine to keep in step.
SUCCESS_STATES: Final = (
    "SUCCESS_PENDING_EXCHANGE",
    "EXCHANGING",
    "EXCHANGED",
    "TOKEN_EXPIRED",
    "EXCHANGE_UNCERTAIN",
)

#: The one state a nameless row may **not** be written by this module. See
#: :func:`apply_pairing`.
_COMMITTED: Final = "EXCHANGED"


class PairingError(Exception):
    """Base. Messages name flow ids, states and column names — never material.

    ``item_id`` is not a secret and is still not printed: it names one of the
    owner's institutions (`AGENTS.md` rule 0), and these messages reach a
    transcript.
    """


class PairingRefused(PairingError):
    """The database disagrees with this recovery, and guessing would fabricate.

    Raised **after** the credential is already durable on every real path, which
    is why it is not fatal to the restore that calls it: the token is not lost and
    must not be exchanged again. What is unresolved is the accounting, and the
    caller's obligation is to report the pairing as still owed rather than to
    treat the whole recovery as failed. :meth:`networth.link_sink.SinkReceipt.complete`
    is how that is said.

    It is an exception rather than a returned value because a caller that has not
    yet spent anything — criterion 7's reconciliation, the criterion 5 rehearsal —
    must stop, and a return value is the one thing that can be ignored in silence.
    The caller that cannot stop is :func:`networth.link_sink.restore`, and
    :attr:`outcome` exists so the conversion it needs is defined here rather than
    assembled at the call site, where "which result does a refusal count as" would
    be a second opinion about this module's own vocabulary.
    """

    @property
    def outcome(self) -> PairingOutcome:
        """This refusal as the value a post-credential caller has to report."""
        return PairingOutcome(result=PairingResult.REFUSED, detail=str(self))


class PairingBadUsage(PairingError):
    """The call itself is wrong — an open transaction, or an empty identifier.

    Separate from :class:`PairingRefused` because it says nothing about the
    owner's data: it is a programming error, and rolling it into the refusal that
    an operator reads would put a bug report in the middle of an emergency
    transcript.
    """


class PairingResult(Enum):
    """What happened, named by what is true afterwards.

    Honest, *not necessarily distinct* — the principle `06a` (iii) settled and
    :class:`~networth.link_crash.CrashState` follows. Two situations that leave
    the same state and the same next action get the same answer.
    """

    #: The nameless success row now names this Item. `26a` can reconcile it.
    WRITTEN = "the flow row now names the recovered Item"

    #: A success row for this flow already names this Item, so the write-back has
    #: already happened. A re-run of a restore reaches this, and so does a
    #: recovery on a host whose database was restored *after* an earlier attempt.
    #: Idempotent on purpose: the alternative is an operator who cannot tell a
    #: finished write-back from a broken one and re-runs until something breaks.
    ALREADY_PAIRED = "the flow row already names the recovered Item"

    #: No success row for this flow exists in this database, so there is nothing
    #: to name. **This is not success.** The criterion's second destination is
    #: *"carried inside the Mac-side emergency artifact"*, and that is where the
    #: pairing still lives; the caller reports it owed. `26a` will not see this
    #: slot at all, which understates what has been spent — the direction issue #7
    #: calls the less harmful one, and still wrong.
    NO_ROW = "no Link success row for this flow exists here"

    #: A success row exists and contradicts this recovery, so nothing was written.
    #: **:func:`apply_pairing` never returns this** — it raises
    #: :class:`PairingRefused`, which is the only form a caller cannot ignore. This
    #: member exists so the one caller that must not stop on a refusal can still
    #: report it in the same vocabulary as every other answer; see
    #: :attr:`PairingRefused.outcome`. Kept distinct from :attr:`NO_ROW` because
    #: "there is no row" and "the row disagrees" are opposite facts about the
    #: database and imply different repairs.
    REFUSED = "a Link success row exists and disagrees with this recovery"


@dataclass(frozen=True, slots=True)
class PairingOutcome:
    """A result plus the observation behind it.

    ``detail`` exists for the same reason
    :class:`~networth.link_crash.Classification` has one: this runs once, under a
    clock, and *"why does it say that"* is not a question the owner will have time
    to answer out of the source.
    """

    result: PairingResult
    detail: str

    @property
    def paired(self) -> bool:
        """Does a row in this database now name the recovered Item?

        False for :attr:`PairingResult.NO_ROW`, which is the point of having a
        property rather than a truthiness test on the enum: *"recovery is not
        reported successful until it is"*, and "there was nowhere to write it" is
        not a write.
        """
        return self.result in (PairingResult.WRITTEN, PairingResult.ALREADY_PAIRED)


@dataclass(frozen=True, slots=True)
class _Row:
    """One row of ``link_success_evidence``, with its own write destination."""

    result_id: str | None
    state: str
    item_id: str | None

    @property
    def legacy(self) -> bool:
        """Whether this row came from the archived scalar ``link_flow`` table.

        The view's second arm projects ``NULL`` as ``result_id`` and excludes any
        legacy row a ``link_result`` already migrated, so the two arms never both
        describe one flow — which is what makes a single ``result_id IS NULL``
        test a complete answer about where the ``UPDATE`` must go.
        """
        return self.result_id is None


def apply_pairing(connection: sqlite3.Connection, *, flow_id: str, item_id: str) -> PairingOutcome:
    """Name the recovered Item on this flow's success row, or say why not.

    Takes no clock. There is no column for *when* a pairing was applied and this
    does not invent one: `07a` owns that table's shape, and a timestamp written
    into a column added for this would be a second record of an event the
    ``item`` row already dates.

    The read and the write are one decision inside ``BEGIN IMMEDIATE``. Not for a
    race this project can currently lose — the restore is one process — but
    because every branch below is chosen *from* the read, so a read outside the
    write lock would make the refusals statements about a past state of the
    database rather than about the one being written to.
    """
    if connection.in_transaction:
        raise PairingBadUsage(
            "the pairing write-back opens its own transaction; pass a connection "
            "with no active one so its read and its write cannot be separated"
        )
    if not flow_id or not item_id:
        # Empty is refused rather than matched. `WHERE flow_id = ''` finds nothing
        # and would be reported as NO_ROW — "this database has no row for your
        # flow" — which is a measurement about the database standing in for a
        # caller that passed nothing.
        raise PairingBadUsage("the pairing write-back needs a non-empty flow_id and item_id")

    connection.execute("BEGIN IMMEDIATE")
    try:
        rows = [
            _Row(result_id=row[0], state=row[1], item_id=row[2])
            for row in connection.execute(
                "SELECT result_id, state, item_id FROM link_success_evidence "
                "WHERE flow_id = ? ORDER BY result_id",
                (flow_id,),
            ).fetchall()
        ]
        outcome = _decide(rows, flow_id=flow_id, item_id=item_id)
        if outcome is not None:
            connection.rollback()
            return outcome
        (candidate,) = [row for row in rows if row.item_id is None]
        _write(connection, candidate, flow_id=flow_id, item_id=item_id)
    except BaseException:
        connection.rollback()
        raise
    connection.commit()
    return PairingOutcome(
        result=PairingResult.WRITTEN,
        detail=(
            f"flow {flow_id} had one Link success row in state {candidate.state} naming "
            f"no Item; it now names the recovered one"
            + (
                # A row the VPS gave up on is still a spent slot, and this
                # recovery just proved the token was live after all. The
                # disagreement is reported rather than refused: refusing would
                # leave in place exactly the double count this criterion exists
                # to remove, in the one case where we know for certain the slot
                # was spent.
                f". Note the row reads {candidate.state} while the exchange that "
                "produced this Item succeeded, so that state predates the recovery"
                if candidate.state == "TOKEN_EXPIRED"
                else ""
            )
        ),
    )


def _decide(rows: list[_Row], *, flow_id: str, item_id: str) -> PairingOutcome | None:
    """Return the outcome, or ``None`` when exactly one row may be written.

    Split out so the decision is readable as a list of shapes rather than as
    control flow wrapped around a transaction. Every ``return`` here leaves the
    database untouched.
    """
    if not rows:
        # Both shapes that get here mean the same thing to the caller and to the
        # owner — there is no row, the artifact is the record — so they are one
        # result with two details rather than two results. The distinction is
        # still worth *printing*: a database that has never heard of the flow is a
        # restore from a backup older than the Link, which is the expected shape
        # of this emergency, while a known flow whose success was never recorded
        # means the VPS died before its poll landed.
        return PairingOutcome(
            result=PairingResult.NO_ROW,
            detail=(
                f"no Link success row for flow {flow_id} exists in this database "
                "(the flow is unknown here, or it is known and its success was never "
                "recorded), so there is no row to name. The pairing remains only in "
                "the recovery artifact"
            ),
        )
    if any(row.item_id == item_id for row in rows):
        return PairingOutcome(
            result=PairingResult.ALREADY_PAIRED,
            detail=f"a Link success row for flow {flow_id} already names this Item",
        )

    committed = [row for row in rows if row.item_id is None and row.state == _COMMITTED]
    if committed:
        # `26a` refuses this row outright rather than counting it, so the double
        # count this criterion is about is not the hazard here. The hazard is the
        # opposite one: `EXCHANGED` means an exchange was committed on the host
        # that died, so writing *this* Item's name on it asserts that this
        # recovery was that exchange. `06a` (ii) measured a second exchange of one
        # public_token as ACCEPTED, so the two may name different Items, and that
        # is criterion 7's three-outcome branch — not a decision to take silently
        # from inside a write-back.
        raise PairingRefused(
            f"flow {flow_id} has a success row in state {_COMMITTED} that names no Item. "
            "An exchange was committed here and its Item identity was lost, so naming it "
            "with this recovery's Item would assert the two are the same exchange. "
            "Reconcile by returned Item identity (07b criterion 7) before writing"
        )

    candidates = [row for row in rows if row.item_id is None]
    if not candidates:
        raise PairingRefused(
            f"every Link success row for flow {flow_id} already names a different Item "
            f"({len(rows)} row(s)), so this recovery's Item has no row to be written to. "
            "Two exchanges of one flow that returned distinct Items each cost a lifetime "
            "slot and both must be accounted (07b criterion 7); adding a row is 07a's "
            "job, not this write-back's"
        )
    if len(candidates) > 1:
        raise PairingRefused(
            f"flow {flow_id} has {len(candidates)} Link success rows naming no Item "
            f"(states {', '.join(sorted(row.state for row in candidates))}), so which one "
            "bought this Item is not decidable here. Writing either would name a flow "
            "that may not have bought it and leave the one that did still nameless"
        )
    return None


def _write(connection: sqlite3.Connection, row: _Row, *, flow_id: str, item_id: str) -> None:
    """Apply the one legal write, conditionally on the row still being nameless.

    ``AND item_id IS NULL`` cannot currently fail — the ``SELECT`` that chose this
    row ran inside the same ``BEGIN IMMEDIATE``, so nothing else can have written
    between them — and it is in the statement anyway, for the reason
    :meth:`networth.link_sink.EmergencyArtifactSink.prepare`'s probe round-trip
    is: it states the contract the surrounding transaction provides, so a later
    caller that drops the transaction gets a refusal here instead of a silent
    overwrite. **It is not a tested guard**, and ``rowcount`` is checked rather
    than asserted so that a future in which it can fail says so.
    """
    if row.legacy:
        # The archived scalar table, for a flow whose evidence was never migrated
        # into `link_result`. `link_flow.flow_id` is UNIQUE, so the flow id is a
        # complete key here; the new arm needs `result_id` because one flow may
        # have several results.
        cursor = connection.execute(
            "UPDATE link_flow SET item_id = ? WHERE flow_id = ? AND item_id IS NULL",
            (item_id, flow_id),
        )
    else:
        cursor = connection.execute(
            "UPDATE link_result SET item_id = ? WHERE result_id = ? AND item_id IS NULL",
            (item_id, row.result_id),
        )
    if cursor.rowcount != 1:
        raise PairingRefused(
            f"the Link success row chosen for flow {flow_id} was no longer nameless when "
            f"the write-back ran ({cursor.rowcount} row(s) updated); nothing was written"
        )
