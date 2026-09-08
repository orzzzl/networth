"""What a Plaid response contained — never what it said.

Task ``06``'s second acceptance criterion is not "the fetch returned 200": it is
that the response was **inspected for the fields net worth actually needs**, and
that what came back was written down. §8.1 derives every age from a *source
clock* (``institution_price_as_of``, ``close_price_as_of``) rather than from a
call succeeding, and §10 needs balances in minor units — so whether Sandbox
supplies those fields at all is an empirical question no document can answer.

That inspection has to happen where the raw SDK object is, and §5 puts the SDK
behind :mod:`networth.plaid.client`. These are the types that seam converts
into, which is what lets the rule "no raw SDK object crosses the boundary" hold
while the observation still describes a real response.

**A presence and a type, never a value.** The Sandbox figures are synthetic, but
a module that prints balances is one edit away from printing real ones, and the
type is the half that decides work anyway: §7 stores money as integer minor
units, so a ``float`` arriving here is a finding for task ``12`` rather than a
detail.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# Distinguishes "the attribute is absent" from "the attribute is present and
# null" — for a source clock those are opposite facts and §8.1 turns on the
# difference. Absent means Plaid does not supply the field for this product at
# all (a design constraint); null means Plaid supplies it and has no value (an
# ``UNKNOWN`` age). Collapsing them is the exact mistake §8.1 exists to prevent,
# one layer down.
_MISSING = object()


@dataclass(frozen=True, slots=True)
class FieldObservation:
    """One field net worth needs, and what the response actually supplied."""

    path: str
    present: bool
    type_name: str | None

    @property
    def note(self) -> str:
        """The one word a report prints for this field."""
        if not self.present:
            return "absent"
        return "null" if self.type_name is None else self.type_name


@dataclass(frozen=True, slots=True)
class RecordSet:
    """One list in a response, folded into one observation per field.

    ``count`` is how many records came back — a shape fact, not a figure. It is
    here because "the fields are all present" means something different over
    zero records than over five, and a report that omitted it could describe an
    empty response as a complete one.
    """

    name: str
    count: int
    fields: tuple[FieldObservation, ...]


def _attribute(record: Any, path: str) -> tuple[bool, str | None]:
    """Walk a dotted path, distinguishing absent from present-and-null."""
    current: Any = record
    for part in path.split("."):
        if current is None:
            return False, None
        nxt = getattr(current, part, _MISSING)
        if nxt is _MISSING:
            # `plaid-python` models raise for unset optional attributes on some
            # versions and return a sentinel on others; a mapping lookup covers
            # the dict-shaped responses without inventing a value.
            if isinstance(current, dict) and part in current:
                nxt = current[part]
            else:
                return False, None
        current = nxt
    if current is None:
        return True, None
    return True, type(current).__name__


def observe(records: Sequence[Any], paths: Sequence[str]) -> tuple[FieldObservation, ...]:
    """Fold a list of records into one observation per field.

    A field counts as present when **any** record carried it, and its type comes
    from the first record supplying a non-null value. Reporting per record would
    turn a fixture's row count into noise; the question being answered is "does
    this response supply this field", not "how many rows came back".
    """
    observations = []
    for path in paths:
        present = False
        type_name: str | None = None
        for record in records:
            record_present, record_type = _attribute(record, path)
            present = present or record_present
            if type_name is None and record_type is not None:
                type_name = record_type
        observations.append(FieldObservation(path=path, present=present, type_name=type_name))
    return tuple(observations)


def record_set(name: str, records: Sequence[Any], paths: Sequence[str]) -> RecordSet:
    """Everything a caller may learn about one list in a response."""
    return RecordSet(name=name, count=len(records), fields=observe(records, paths))
