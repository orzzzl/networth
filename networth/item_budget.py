"""How many of the ten lifetime Plaid Item slots are left, and why.

**F2**: there are ten Items for the lifetime of the account and `/item/remove`
never returns one.  **F2a**: a slot is spent the moment a Link session
*succeeds* — not at the exchange, and not when the `access_token` is stored.
Together those make this count the only currency section 14 has: reserve is
literally the number of link mistakes this project can survive.

Two tables hold evidence of a succeeded Link, and the whole job of this module
is to reconcile them **by Item identity** so one slot is never counted twice:

- an ``item`` row — the exchange finished and the token was stored;
- a ``link_flow`` row whose state follows a completed Link but which has no
  ``item`` row (yet, or ever).

Where several ``link_flow`` rows describe the same Item, the slot is classified
from **all** of them at once. Reading whichever row was written first made the
same pair of rows report two different things depending on their insertion
order, which is not a fact about the Item.

Section 7's state table is the authority on which states those are, and it is
narrower than it looks.  A URL the owner never opened (``URL_MINTED``,
``URL_EXPIRED``), a session he exited (``SESSION_EXITED``) and a flow he
abandoned (``ABANDONED``) cost **nothing** — reporting them as spent would train
him to ignore the one count that matters, which is why this module decides
spentness from the **state alone** and never from a deadline compared against
the clock.

This module returns facts.  Formatting them for a human is task 26, and there is
deliberately no second place that does this arithmetic: two sources would mean
two answers.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

#: Section 14 / **F2**. Lifetime, never recycled.
LIFETIME_ITEM_SLOTS = 10

#: Section 7: states reached *after* a Link session succeeded but that have no
#: ``item`` row of their own. ``EXCHANGED`` is spent too and is deliberately
#: absent — it is counted through the ``item`` row it wrote.
_STRANDED_STATES = ("TOKEN_EXPIRED", "EXCHANGE_UNCERTAIN")
_IN_FLIGHT_STATES = ("SUCCESS_PENDING_EXCHANGE", "EXCHANGING")

#: Every state whose row may still be the only record of a spent slot.
#: ``EXCHANGED`` is included so that a row whose ``item`` row is missing is not
#: silently free; the reconciliation below drops it when the ``item`` row exists.
_FLOW_STATES_AFTER_A_COMPLETED_LINK = (*_STRANDED_STATES, *_IN_FLIGHT_STATES, "EXCHANGED")

#: Section 7's table, "Slot spent? = **no**": a URL never opened, a session the
#: owner started or exited, a URL that expired unopened, a flow he abandoned.
#: Nothing below reads this tuple — it exists so the classification is **total**
#: and can be compared against the schema's CHECK in both directions. An
#: eleventh state added to the schema and to neither tuple costs nothing by
#: default, which is the optimistic direction; naming the free states is what
#: lets a test see that omission instead of confirming what it already believes.
_SPENDS_NO_SLOT = ("URL_MINTED", "SESSION_STARTED", "SESSION_EXITED", "URL_EXPIRED", "ABANDONED")

#: Every state this module has decided about. The schema's CHECK must admit
#: exactly these — see ``test_the_schema_and_this_module_classify_the_same_states``.
_CLASSIFIED_STATES = frozenset((*_SPENDS_NO_SLOT, *_FLOW_STATES_AFTER_A_COMPLETED_LINK))


class ItemBudgetError(RuntimeError):
    """A stored row cannot answer the slot count truthfully."""


class SlotEvidence(StrEnum):
    """Why a slot is recorded as spent — the provenance of one unit of cost."""

    #: An ``item`` row exists: the token was stored and the Item is usable.
    ITEM = "ITEM"
    #: ``TOKEN_EXPIRED`` or ``EXCHANGE_UNCERTAIN``: spent, and not usable.
    STRANDED_FLOW = "STRANDED_FLOW"
    #: ``SUCCESS_PENDING_EXCHANGE`` or ``EXCHANGING``: spent, outcome not known
    #: yet. Each becomes either a usable Item or a stranded slot.
    IN_FLIGHT_FLOW = "IN_FLIGHT_FLOW"
    #: An ``EXCHANGED`` row this read could not match to an ``item`` row —
    #: either it names no Item or it names one with no row. Section 7 makes that
    #: state terminal and says the ``item`` row was committed *before* it was
    #: entered, so the stored rows contradict each other. The slot is spent
    #: either way (**F2a**); what is unknown is whether the Item is usable, and
    #: an unnamed Item may also already be counted through its own row. Both
    #: directions of error are possible here, which is exactly why this is
    #: reported as a fault to look at rather than folded into a neighbouring
    #: state and guessed at.
    ORPHANED_FLOW = "ORPHANED_FLOW"


@dataclass(frozen=True, slots=True)
class SpentSlot:
    """One lifetime slot that is gone, and the evidence that it is gone."""

    evidence: SlotEvidence
    #: Plaid's `item_id`. ``None`` only for a flow that failed before the
    #: exchange returned one — the slot is still spent (**F2a**), it simply
    #: cannot be named to Plaid support.
    plaid_item_id: str | None
    #: The ``link_flow.flow_id`` this was read from; ``None`` for ITEM evidence.
    flow_id: str | None
    #: The ``link_flow.state``; ``None`` for ITEM evidence.
    state: str | None
    #: Set when this Item replaced another one, which cost a *second* slot
    #: (**F2**: the first was not returned). Makes a replacement's cost visible.
    replaces_plaid_item_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, SlotEvidence):
            raise TypeError("evidence must be a SlotEvidence")
        for field in ("plaid_item_id", "flow_id", "state", "replaces_plaid_item_id"):
            value = cast(str | None, getattr(self, field))
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field} must be non-empty when present")
        if (self.evidence is SlotEvidence.ITEM) != (self.flow_id is None):
            raise ValueError("flow evidence requires a flow_id and ITEM evidence forbids one")
        if self.evidence is SlotEvidence.ITEM:
            if self.plaid_item_id is None:
                raise ValueError("ITEM evidence requires a plaid_item_id")
        elif self.replaces_plaid_item_id is not None:
            raise ValueError("only an item row records a replacement")


@dataclass(frozen=True, slots=True)
class ItemBudget:
    """The remaining-slot count together with everything behind it.

    A caller can explain the number rather than only print it: every unit of
    cost is one entry in :attr:`spent`, carrying where it was read from.
    """

    capacity: int
    spent: tuple[SpentSlot, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.capacity, int) or isinstance(self.capacity, bool):
            raise TypeError("capacity must be an integer")
        if self.capacity <= 0:
            raise ValueError("capacity must be positive")
        if not all(isinstance(slot, SpentSlot) for slot in self.spent):
            raise TypeError("spent must hold SpentSlot records")

    @property
    def spent_count(self) -> int:
        return len(self.spent)

    @property
    def remaining(self) -> int:
        """Slots left.

        Deliberately **not** clamped at zero. Plaid stops issuing Items past
        ten, so a negative answer means the database disagrees with **F2** —
        a fault to surface, not headroom to round away.
        """

        return self.capacity - self.spent_count

    @property
    def usable(self) -> tuple[SpentSlot, ...]:
        """Spent slots that produced a usable Item."""

        return tuple(s for s in self.spent if s.evidence is SlotEvidence.ITEM)

    @property
    def stranded(self) -> tuple[SpentSlot, ...]:
        """Spent slots with no usable Item and no outcome still pending."""

        return tuple(s for s in self.spent if s.evidence is SlotEvidence.STRANDED_FLOW)

    @property
    def in_flight(self) -> tuple[SpentSlot, ...]:
        """Slots a completed Link has spent whose exchange has not resolved.

        Reported separately because their cost is certain (**F2a**) while their
        *outcome* is not: each becomes either a usable Item or a stranded slot.
        """

        return tuple(s for s in self.spent if s.evidence is SlotEvidence.IN_FLIGHT_FLOW)

    @property
    def orphaned(self) -> tuple[SpentSlot, ...]:
        """Spent slots whose stored evidence contradicts section 7.

        Non-empty means the database needs looking at before the count is acted
        on — see :attr:`SlotEvidence.ORPHANED_FLOW`.
        """

        return tuple(s for s in self.spent if s.evidence is SlotEvidence.ORPHANED_FLOW)

    @property
    def replacements(self) -> tuple[SpentSlot, ...]:
        """Spent slots held by an Item that replaced an earlier one."""

        return tuple(s for s in self.spent if s.replaces_plaid_item_id is not None)


@dataclass(frozen=True, slots=True)
class _FlowRow:
    """One ``link_flow`` row, reduced to the three columns this read uses."""

    flow_id: str
    state: str
    item_id: str | None


def _classify(rows: list[_FlowRow]) -> tuple[SlotEvidence, _FlowRow]:
    """Read one spent slot from *every* flow row that evidences it.

    Returns the evidence and the row that decided it. Both are functions of the
    rows themselves and never of the order they were written in — the tie-break
    is ``flow_id`` (unique, and the flow's own name) rather than the rowid,
    which is only the order somebody inserted them.

    The precedence is section 7's own: ``EXCHANGED`` is terminal and promises a
    committed ``item`` row, so a group carrying one that this read could not
    match is a contradiction and that is the most important true thing about the
    slot. Failing that, a terminal stranded outcome outranks a pending one,
    because it is known.
    """

    exchanged = [row for row in rows if row.state == "EXCHANGED"]
    if exchanged:
        return SlotEvidence.ORPHANED_FLOW, min(exchanged, key=lambda row: row.flow_id)
    stranded = [row for row in rows if row.state in _STRANDED_STATES]
    if stranded:
        return SlotEvidence.STRANDED_FLOW, min(stranded, key=lambda row: row.flow_id)
    return SlotEvidence.IN_FLIGHT_FLOW, min(rows, key=lambda row: row.flow_id)


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ItemBudgetError(f"{field} must be non-empty text")
    return value


def _optional_text(value: object, *, field: str) -> str | None:
    return None if value is None else _text(value, field=field)


def read_item_budget(connection: sqlite3.Connection) -> ItemBudget:
    """Answer how many lifetime Item slots are left, with the facts behind it.

    Reads only; the caller owns the transaction. Every spent slot is counted
    exactly once even when both tables describe it — which is what makes an
    Item recovered by task 07b cost one slot rather than two, whichever host
    ended up holding the credential.
    """

    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be a sqlite3.Connection")

    spent: list[SpentSlot] = []
    known_item_ids: set[str] = set()

    for row in connection.execute(
        """
        SELECT i.plaid_item_id, i.replaces_item_id, replaced.plaid_item_id
        FROM item AS i
        LEFT JOIN item AS replaced ON replaced.id = i.replaces_item_id
        ORDER BY i.id
        """
    ).fetchall():
        plaid_item_id = _text(row[0], field="item.plaid_item_id")
        replaces_id, replaced_plaid_item_id = row[1], row[2]
        if replaces_id is not None and replaced_plaid_item_id is None:
            # The FK that forbids this is per connection and defaults off, so a
            # row can outlive the guarantee that wrote it. Reporting a
            # replacement chain that silently loses a link would understate
            # where the slots went, so refuse instead.
            raise ItemBudgetError(f"item {plaid_item_id!r} replaces a row that no longer exists")
        known_item_ids.add(plaid_item_id)
        spent.append(
            SpentSlot(
                evidence=SlotEvidence.ITEM,
                plaid_item_id=plaid_item_id,
                flow_id=None,
                state=None,
                replaces_plaid_item_id=_optional_text(
                    replaced_plaid_item_id, field="item.replaces_item_id"
                ),
            )
        )

    placeholders = ", ".join("?" * len(_FLOW_STATES_AFTER_A_COMPLETED_LINK))
    slots_of_flows: list[list[_FlowRow]] = []
    by_item: dict[str, list[_FlowRow]] = {}
    for row in connection.execute(
        f"""
        SELECT flow_id, state, item_id
        FROM link_flow
        WHERE state IN ({placeholders})
        ORDER BY id
        """,  # noqa: S608 — placeholders only, from a module-level tuple
        _FLOW_STATES_AFTER_A_COMPLETED_LINK,
    ).fetchall():
        flow = _FlowRow(
            flow_id=_text(row[0], field="link_flow.flow_id"),
            state=_text(row[1], field="link_flow.state"),
            item_id=_optional_text(row[2], field="link_flow.item_id"),
        )

        if flow.item_id is None:
            # No Item identity to reconcile against, so this row is its own
            # slot: two rows sharing a NULL are two separate Link successes.
            slots_of_flows.append([flow])
            continue
        if flow.item_id in known_item_ids:
            continue  # already counted through its `item` row
        group = by_item.get(flow.item_id)
        if group is None:
            group = []
            by_item[flow.item_id] = group
            slots_of_flows.append(group)
        group.append(flow)

    for group_rows in slots_of_flows:
        evidence, deciding = _classify(group_rows)
        spent.append(
            SpentSlot(
                evidence=evidence,
                plaid_item_id=deciding.item_id,
                flow_id=deciding.flow_id,
                state=deciding.state,
                replaces_plaid_item_id=None,
            )
        )

    return ItemBudget(capacity=LIFETIME_ITEM_SLOTS, spent=tuple(spent))
