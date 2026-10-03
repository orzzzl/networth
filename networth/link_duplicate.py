"""Two exchanges of one flow, told apart by the Item each one returned.

`07b` criterion 7 — *"the losing branch is classified from a measurement, not a
guess"* — and criterion 8, which is that branch's three shapes tested with
synthetic responses. Two modules already ship refusal text naming this one, so it
is owed rather than speculative: :func:`networth.link_pairing.apply_pairing` stops
with *"Reconcile by returned Item identity (07b criterion 7) before writing"* on a
nameless ``EXCHANGED`` row, and with *"Two exchanges of one flow that returned
distinct Items each cost a lifetime slot and both must be accounted (07b criterion
7)"* when every row already names a different Item. Until now those sentences
pointed at nothing.

**Why the branch cannot be keyed on an error.** The obvious design is "the
`public_token` is single-use, so the loser gets a failure" — and it is wrong here.
`06a` (ii) measured the second exchange of one `public_token` in Sandbox on
2026-09-16/17: it was **ACCEPTED**, and the first `access_token` stayed
**HEALTHY**. So at-most-once is what this project assumed Plaid enforces, not what
it observed, and *both hosts succeeding is an expected outcome* rather than an
impossible one. What the responses do carry is an ``item_id``, so that is what this
module reads.

**The unmeasured half is the expensive half, and it is why there are three
outcomes and not two.** Whether an accepted duplicate consumes a *second* lifetime
Item was not established — a duplicate that is free and one that silently spends a
slot look identical from here. So identity decides the cost:

* **same Item** — one entry, one slot. Both credentials are for the same Item, so
  neither is the only copy of anything and deduplicating discards nothing.
* **distinct Items** — both retained, both counted. This is the case that costs a
  slot, and collapsing it *"discards the only credential for the second Item and
  hides a consumed lifetime slot"*, turning an accounting question into an
  unrecoverable one (**F2a**: slots never come back).
* **not establishable** — an explicit unresolved answer, in the same spirit as
  `06a` (iii)'s irreducible window. Not a default to either branch above, and
  nothing is discarded while the question is open.

**What this module found, which is a sentence rather than a missing feature — the
same shape as the one :mod:`networth.link_crash` exists to fix, in the same file
family.** The place the "old VPS comes back" scenario actually lands is
:func:`networth.link_sink.restore` on the recovered host, and the credential
collision there was reported by
:meth:`~networth.link_sink.ReplacementHostSink.prepare_for` as:

    already holds an access token for flow {flow_id}; it will not be overwritten.
    **If that material is the recovery you are repeating, the exchange is already
    done**

That closing clause is a *same-Item assertion made without comparing the Items*,
and both identities are in hand when it is printed: one is sealed in the artifact
this restore just opened, the other is on the :class:`~networth.tokenstore.SecretRecord`
the refusal just read. Told that the exchange is already done, the owner concludes
the artifact is redundant — and in the distinct-Items case the artifact is the only
copy of the other Item's credential and the only record that a second slot is
gone. The verb's own follow-up line made it worse by inviting the repair: *"this
verb can be re-run once the reason above is fixed"*, where "the reason" is a
credential already in the store and "fixing" it means moving that credential
aside. `link_crash` was written because ``prepare``'s *"Move it aside — it may hold
an earlier recovery"* said the same thing about the artifact.

**The refusal itself was right and stays.** :meth:`~networth.tokenstore.TokenStore.put`
publishes with :func:`os.link` and raises rather than overwriting, which is
criterion 8's *"`TokenStore` never silently prefers one credential over another"*
holding structurally rather than by policy. Nothing here relaxes it: this module
adds no second name under which a duplicate could be stored, because the store's
naming scheme is ``access_token.<flow_id>`` and a second credential for one flow
belongs to `07a`'s ``link_result`` row, not to an emergency verb inventing a
format. In the distinct-Items case *both credentials are already retained* — one in
the store, one in the artifact — and what was missing was saying so.

**This is a reconciler of last resort and not permission to race.** Criterion 7 is
explicit: *"the at-most-one exchange claim and the fence stay mandatory ... prefer
a design that does not race at all over one that races and reconciles."* The fence
— the owner powering off the old instance and typing the attestation — is what
keeps this rare. This module exists for the case where it failed anyway, which is
exactly the case a recovery procedure is not allowed to be unable to describe.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from networth.tokenstore import Secret, TokenStore, TokenStoreError

#: ``link_success_evidence`` states that prove an exchange was *attempted* on this
#: host. A row in one of these naming no Item is an exchange whose identity was
#: lost, which is unresolved rather than absent.
#:
#: The other two success states are deliberately **not** here, and each for its own
#: reason. ``SUCCESS_PENDING_EXCHANGE`` is the ordinary, expected shape of this whole
#: emergency — the Link succeeded and the VPS died *before* exchanging — so reading
#: it as duplicate evidence would report every healthy recovery as unresolved.
#: ``TOKEN_EXPIRED`` is the host giving up on a token without exchanging it, which
#: :func:`networth.link_pairing.apply_pairing` already treats as the same slot this
#: recovery spent, in writing.
ATTEMPTED_EXCHANGE_STATES: Final = ("EXCHANGING", "EXCHANGED", "EXCHANGE_UNCERTAIN")


class DuplicateOutcome(Enum):
    """Criterion 7's three outcomes, named by what is true afterwards.

    Honest, *not necessarily distinct* — the principle `06a` (iii) settled and
    :class:`~networth.link_crash.CrashState` and
    :class:`~networth.link_pairing.PairingResult` both follow.
    """

    #: Every exchange this host can see returned the Item this recovery returned.
    #: One entry, one slot. Reached with no duplicate evidence at all, which is
    #: the same answer for the same reason: one Item, one slot.
    SAME_ITEM = "every exchange recorded here returned the same Item"

    #: An exchange recorded here returned a *different* Item. Both credentials
    #: must be kept and both slots counted; this is the case that costs a slot.
    DISTINCT_ITEMS = "an exchange recorded here returned a different Item"

    #: An exchange was recorded here and which Item it returned cannot be
    #: established, or material exists whose durability cannot be established.
    #: The slot count is not knowable and is not guessed.
    UNRESOLVED = "an exchange is recorded here whose Item identity is not establishable"


class DuplicateSource(Enum):
    """Where one piece of evidence came from, and whether it holds a credential.

    The distinction is load-bearing for :attr:`DuplicateVerdict.keep`: a
    ``TokenStore`` record *is* a copy of an ``access_token``, while a ``link_result``
    row is accounting that merely references one. Deleting the first can lose an
    Item; deleting the second cannot.
    """

    TOKEN_STORE = "this host's TokenStore"
    FLOW_ROW = "a Link success row in this host's database"

    @property
    def holds_a_credential(self) -> bool:
        return self is DuplicateSource.TOKEN_STORE


@dataclass(frozen=True, slots=True)
class DuplicateEvidence:
    """One record of an exchange on this host, and the Item it names.

    ``item_id`` is ``None`` for an exchange whose identity was lost — a nameless
    ``EXCHANGED`` row, or material whose durability barrier could not be completed.
    That is the input that produces :attr:`DuplicateOutcome.UNRESOLVED`, and it is
    kept separate from "no evidence" precisely because the two are opposite facts.
    """

    source: DuplicateSource
    where: str
    item_id: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class DuplicateVerdict:
    """What the identities prove, plus the cost and the places that must survive.

    ``detail`` exists for the reason :class:`~networth.link_crash.Classification`
    and :class:`~networth.link_pairing.PairingOutcome` have one: this runs once,
    under a 30-minute clock, and *"why does it say that"* is not a question the
    owner will have time to answer out of the source.
    """

    outcome: DuplicateOutcome
    detail: str

    #: Every distinct Item identity established for this flow, sorted. Always
    #: contains this recovery's own, which came sealed in the artifact.
    identities: tuple[str, ...]

    #: Lifetime slots this flow provably spent (**F2a**), or ``None`` when that is
    #: not knowable. ``None`` rather than a number for the same reason
    #: :func:`networth.item_budget.read_item_budget` raises instead of answering:
    #: a caller cannot tell a guessed integer from a measured one.
    slots: int | None

    #: Every place whose deletion would lose the only copy of some Item's
    #: credential. Read *before* the restore writes anything, so this recovery's
    #: own artifact is listed until its credential lands somewhere else.
    keep: tuple[str, ...]

    evidence: tuple[DuplicateEvidence, ...]

    @property
    def duplicate(self) -> bool:
        """Is there a second exchange here at all?

        A separate question from the outcome: :attr:`DuplicateOutcome.SAME_ITEM`
        is the answer both for "no other exchange is recorded" and for "another
        exchange returned this same Item", because the cost and the next action
        are identical. This is how a caller tells the transcript apart.
        """
        return bool(self.evidence)

    @property
    def second_credential_here(self) -> bool:
        """Does this host already hold a credential for this flow?

        The condition on which a restore must refuse rather than write: the
        ``TokenStore`` would refuse anyway, and refusing here means doing it with
        both Item identities in hand instead of neither.
        """
        return any(record.source.holds_a_credential for record in self.evidence)


def classify_duplicate(
    *,
    flow_id: str,
    recovered_item_id: str,
    recovered_where: str,
    evidence: tuple[DuplicateEvidence, ...],
) -> DuplicateVerdict:
    """Criterion 7's branch: three outcomes, chosen on returned Item identity.

    Pure — no store, no database, no clock. That is what makes criterion 8's
    *"cover all three shapes with synthetic responses"* a thing a test can do
    without a live exchange: the shapes **are** the identities, and every caller
    that has to read a disk to find them does it in
    :func:`gather_duplicate_evidence`.

    ``recovered_item_id`` is this recovery's own returned identity and is never
    optional. It arrives from :func:`networth.link_sink.read_artifact`, which has
    already validated that the field is present, so an unknown identity on *this*
    side is not a state this function has to represent — and accepting ``None``
    here would invite a caller to pass one rather than stop.
    """
    if not flow_id or not recovered_item_id or not recovered_where:
        # Refused rather than defaulted: an empty identity compares unequal to
        # every real one, so it would be reported as DISTINCT_ITEMS — a fabricated
        # second slot, which is the expensive direction.
        raise ValueError("classifying a duplicate needs a flow id, an Item identity and a place")

    unnamed = tuple(record for record in evidence if record.item_id is None)
    others = tuple(record for record in evidence if record.item_id is not None)
    identities = tuple(
        sorted({recovered_item_id, *(record.item_id for record in evidence if record.item_id)})
    )

    # Item identity → every place holding a credential for it. The empty key
    # collects credentials whose Item is not establishable; a real ``item_id`` is
    # never empty, so it cannot collide with one.
    held: dict[str, set[str]] = {recovered_item_id: {recovered_where}}
    for record in evidence:
        if record.source.holds_a_credential:
            held.setdefault(record.item_id or "", set()).add(record.where)
    sole = tuple(sorted(next(iter(places)) for places in held.values() if len(places) == 1))

    if unnamed:
        return DuplicateVerdict(
            outcome=DuplicateOutcome.UNRESOLVED,
            detail=(
                f"flow {flow_id} has {len(unnamed)} record(s) of an exchange on this host "
                f"whose Item identity is not establishable ("
                + "; ".join(f"{record.where}: {record.detail}" for record in unnamed)
                + "), so whether this flow spent one lifetime slot or two is not knowable "
                "here. Nothing may be deleted and nothing may be exchanged again"
            ),
            identities=identities,
            slots=None,
            # Every holder, not only the sole ones: while the identities are open,
            # a second copy cannot be shown to be a copy *of the same Item*.
            keep=tuple(sorted({place for places in held.values() for place in places})),
            evidence=evidence,
        )

    distinct = tuple(record for record in others if record.item_id != recovered_item_id)
    if distinct:
        return DuplicateVerdict(
            outcome=DuplicateOutcome.DISTINCT_ITEMS,
            detail=(
                f"flow {flow_id} was exchanged more than once and the responses returned "
                f"{len(identities)} distinct Items ("
                + "; ".join(f"{record.where}: {record.detail}" for record in distinct)
                + f"), so {len(identities)} lifetime slots are spent and every credential "
                "must be kept. Deduplicating would discard the only credential for one of "
                "them and hide the slot it cost"
            ),
            identities=identities,
            slots=len(identities),
            keep=sole,
            evidence=evidence,
        )

    return DuplicateVerdict(
        outcome=DuplicateOutcome.SAME_ITEM,
        detail=(
            (
                f"flow {flow_id} was exchanged more than once and every response returned "
                "the same Item ("
                + "; ".join(f"{record.where}: {record.detail}" for record in others)
                + "), so one lifetime slot is spent and the credentials are "
                "interchangeable"
            )
            if others
            else (
                f"no other exchange of flow {flow_id} is recorded on this host, so one "
                "lifetime slot is spent"
            )
        ),
        identities=identities,
        slots=1,
        keep=sole,
        evidence=evidence,
    )


def gather_duplicate_evidence(
    *,
    flow_id: str,
    recovered_token: Secret,
    token_store_directory: Path,
    connection: sqlite3.Connection | None,
) -> tuple[DuplicateEvidence, ...]:
    """Read every local record of an exchange of this flow **other than this one**.

    Reads only. ``connection`` may be ``None`` — a replacement host may have a
    ``TokenStore`` before it has a database — and that is a narrower question
    rather than a safer one: without the database, a second exchange recorded only
    in a ``link_result`` row is invisible here. The caller that has a connection
    must pass it, which is why :func:`networth.link_sink.restore` requires the
    argument it may set to ``None``.

    **``recovered_token`` is how "another exchange" is told from "this one, already
    restored", and the Item identity cannot do it.** A restore that has already run
    leaves its own credential under the very name a second run collides with, and
    that record names the same Item this recovery returned — indistinguishable from
    the old VPS having exchanged and got the same Item, if identity is all you look
    at. The material separates them: Plaid issues a *new* ``access_token`` per
    exchange, which is why `06a` (ii) could observe the first one still HEALTHY
    beside an accepted duplicate. Equal material is therefore one exchange seen
    twice, and it is **not** duplicate evidence: the refusal that belongs to it is
    :meth:`~networth.link_sink.ReplacementHostSink.prepare_for`'s *"if that material
    is the recovery you are repeating, the exchange is already done"*, which was
    always right about this case and was only wrong about being said without
    checking. The material never leaves this call.

    The one way that comparison can mislead is equal material with *distinct* Items,
    and it cannot happen: one ``access_token`` names one Item.
    """
    records: list[DuplicateEvidence] = []
    store = TokenStore(token_store_directory)
    where = f"the TokenStore at {token_store_directory}"
    try:
        existing = store.reconcile(flow_id)
        same_material = existing is not None and (
            store.get(existing.secret_ref).reveal() == recovered_token.reveal()
        )
    except (TokenStoreError, OSError) as exc:
        # `reconcile` raises `UnverifiedMaterial` when it finds material under the
        # pending name and cannot complete the durability barrier. Both "exchange
        # again" and "store over it" spend or lose an Item, so this is the
        # unresolved input rather than either answer. `get` is inside the same
        # `try` deliberately: material that is present and unreadable is the same
        # unanswered question as material that is present and unverifiable.
        records.append(
            DuplicateEvidence(
                source=DuplicateSource.TOKEN_STORE,
                where=where,
                item_id=None,
                detail=f"holds material for this flow that it cannot verify: {exc}",
            )
        )
    else:
        if existing is not None and not same_material:
            records.append(
                DuplicateEvidence(
                    source=DuplicateSource.TOKEN_STORE,
                    where=where,
                    item_id=existing.item_id,
                    detail=(
                        "already holds a different access token for this flow"
                        if existing.item_id is not None
                        else (
                            "already holds a different access token for this flow whose "
                            "record names no Item, so which exchange produced it is not "
                            "establishable"
                        )
                    ),
                )
            )

    if connection is None:
        return tuple(records)

    for result_id, state, item_id in connection.execute(
        "SELECT result_id, state, item_id FROM link_success_evidence "
        "WHERE flow_id = ? ORDER BY result_id",
        (flow_id,),
    ).fetchall():
        # A row naming an Item is a returned identity whatever its state: some
        # exchange produced it. A *nameless* row is evidence only when its state
        # says an exchange was attempted — see `ATTEMPTED_EXCHANGE_STATES`.
        if item_id is None and state not in ATTEMPTED_EXCHANGE_STATES:
            continue
        records.append(
            DuplicateEvidence(
                source=DuplicateSource.FLOW_ROW,
                where=(
                    f"link_result {result_id}"
                    if result_id is not None
                    else f"the legacy link_flow row for {flow_id}"
                ),
                item_id=item_id,
                detail=(
                    f"state {state} and it names an Item"
                    if item_id is not None
                    else f"state {state} and it names no Item, so the exchange it "
                    "records cannot be attributed"
                ),
            )
        )
    return tuple(records)
