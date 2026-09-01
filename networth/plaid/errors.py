"""The one place a Plaid error becomes one of our Item states.

``DESIGN.md`` section 8.2 draws four states and the transitions between them.
This module is the only implementation of that diagram: every other component
asks it rather than reading ``error_code`` itself, because a second reader is a
second opinion about whether the owner needs to act.

Two rules shape everything here.

**An unknown code is never healthy.** A taxonomy that quietly maps what it does
not recognise onto "fine" is how a broken connection renders as a live number,
which is the failure this whole product exists to refuse. Unknown codes get a
*provisional* state and are flagged :attr:`Classification.recognised` ``False``
so ``doctor`` and the payload can surface them as what they are: something we
have not seen before.

**Health is proved, never inferred from an absence.** :data:`HEALTHY` has
exactly one producer — a caller holding a Plaid response that contained an Item
and no error object at all. No function here returns it, and in particular
:func:`classify_error` cannot, because it is only ever called when an error
object *is* present. *(Round 1 of review: this module's entry point returned
``HEALTHY`` for ``error_code=None`` whatever else it was told, so an
``error_type`` with no code — and, through the client, a response missing its
Item entirely — came out healthy. "No code" is not the same fact as "no error",
and the difference is a dead connection rendering as a live number.)*

**The code table holds only codes this project's own design names.** Plaid's
catalogue is large and this module cannot verify it from here, so writing it out
from memory would put unverified claims in the position that decides whether the
owner is told his bank connection is dead. Codes arrive here by being named in
``DESIGN.md``; everything else is classified by ``error_type``, which the SDK
does enumerate (``tests/test_plaid_errors.py`` asserts our type table against
it), and is marked unrecognised.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class ItemState(StrEnum):
    """Axis A of section 8. Data age is Axis B and is not on this axis at all.

    The values are the strings the ``item.status`` column checks (task 03's
    ``0001_initial.sql``); one vocabulary, so a state cannot be valid in the
    taxonomy and rejected by the schema.
    """

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    NEEDS_REAUTH = "NEEDS_REAUTH"
    REVOKED = "REVOKED"

    @property
    def owner_actionable(self) -> bool:
        """Does this state ask the owner to do something?

        ``DEGRADED`` deliberately does not. "The institution is down" and "your
        connection is dead" are different facts, and alerting the same way for
        both is where alert fatigue starts (section 8.2).
        """
        return self in (ItemState.NEEDS_REAUTH, ItemState.REVOKED)


# Codes named by DESIGN.md section 8.2 and by task 05's entry. Adding to this
# table is a design change: the entry says which codes must be covered, and each
# one below points at the sentence that put it here.
_CODE_STATES: dict[str, ItemState] = {
    # Section 8.2: the two doors into NEEDS_REAUTH. Both are repaired by Link in
    # *update mode* (section 8.3), which reuses the same Item and spends no slot.
    "ITEM_LOGIN_REQUIRED": ItemState.NEEDS_REAUTH,
    "PENDING_EXPIRATION": ItemState.NEEDS_REAUTH,
    # Section 8.2 names ITEM_NOT_FOUND; section 8.4 names USER_PERMISSION_REVOKED
    # as "the event that lands in REVOKED", noting Plaid says it "may not be
    # possible to launch update mode" — which is exactly what separates this
    # state from the one above, because leaving it costs a lifetime slot (F2).
    "ITEM_NOT_FOUND": ItemState.REVOKED,
    "USER_PERMISSION_REVOKED": ItemState.REVOKED,
}

# Recognised, and deliberately *not* a transition (section 8.2, F8). An Item
# scheduled for migration is not in an error state, which is why `/item/get`
# cannot see this and why v0 will never poll it into existence (section 8.4).
# It is here so that if Plaid ever does surface it, it is handled rather than
# classified as an unknown ITEM_ERROR — and it must not move the Item to
# NEEDS_REAUTH, which would make an advance warning look like a live outage.
_NO_TRANSITION_CODES = frozenset({"PENDING_DISCONNECT"})

# The fallback, by error_type. These are provisional: they decide how an Item
# behaves until someone reads the flag and extends the table above.
_TYPE_STATES: dict[str, ItemState] = {
    # Section 8.2's DEGRADED row, by type rather than by guessing at the codes
    # inside it: the institution is having a problem, we retry with backoff and
    # the owner is not asked to do anything.
    "INSTITUTION_ERROR": ItemState.DEGRADED,
    "RATE_LIMIT_EXCEEDED": ItemState.DEGRADED,
    "API_ERROR": ItemState.DEGRADED,
    # An ITEM_ERROR whose code is not in the table is the case that matters
    # most, and it still resolves to DEGRADED rather than NEEDS_REAUTH: we do
    # not know that the owner can fix it, and a false "your bank needs you" is
    # the alert he learns to ignore before the true one arrives. It is loud in
    # the honest way instead — `recognised` is False.
    "ITEM_ERROR": ItemState.DEGRADED,
    # Our own call was malformed or our token was rejected. Not the owner's
    # problem to fix, and not evidence about the connection either.
    "INVALID_REQUEST": ItemState.DEGRADED,
    "INVALID_INPUT": ItemState.DEGRADED,
}

_UNKNOWN_TYPE_STATE = ItemState.DEGRADED


@dataclass(frozen=True, slots=True)
class Classification:
    """What one Plaid response means for one Item.

    ``state is None`` means *recognised, and not a transition on this axis* —
    the Item keeps whatever state it already had. Only ``PENDING_DISCONNECT``
    produces it today. Callers must treat ``None`` as "leave it alone" rather
    than as a default state, which is why it is not spelled ``HEALTHY``.

    Read health off :attr:`state`, never off ``error_code is None``:
    :func:`transport_failure` is a ``DEGRADED`` result with no code, because
    Plaid never answered. The state field is the answer; the code is provenance.
    """

    state: ItemState | None
    recognised: bool
    error_code: str | None
    error_type: str | None
    detail: str

    @property
    def owner_actionable(self) -> bool:
        return self.state is not None and self.state.owner_actionable


HEALTHY = Classification(
    state=ItemState.HEALTHY,
    recognised=True,
    error_code=None,
    error_type=None,
    detail="the call succeeded and the Item carries no error",
)


def malformed_response(detail: str) -> Classification:
    """Plaid answered, and the answer was not one we can read.

    A response with no Item, or an Item with no ``error`` field at all, is not
    evidence of health — it is evidence that something changed underneath us.
    ``DEGRADED``, and ``recognised=False`` so it surfaces as the anomaly it is
    rather than sitting quietly in the same bucket as a bank outage.
    """
    return Classification(
        state=ItemState.DEGRADED,
        recognised=False,
        error_code=None,
        error_type=None,
        detail=f"unreadable Plaid response: {detail}",
    )


def classify_error(error_code: str | None, error_type: str | None) -> Classification:
    """Map one *present* Plaid error onto Axis A. Never returns ``HEALTHY``.

    Call this only when an error object exists. Both fields may still be
    ``None`` — an error object that carries neither a code nor a type is
    something we have never seen, and it is classified as an unrecognised
    ``DEGRADED`` rather than waved through, because the fact that decides health
    is the presence of the error object, not the population of its fields.

    Neither argument may be an ``error_message``: those can quote request
    content, and this result is written to logs and into the payload
    (``AGENTS.md`` rule 1). Codes and types are closed vocabularies from Plaid,
    not data about the owner's accounts.
    """
    if error_code is None:
        provisional = _TYPE_STATES.get(error_type or "", _UNKNOWN_TYPE_STATE)
        logger.warning(
            "Plaid error object with no error_code (type %r); treating the Item as %s "
            "provisionally",
            error_type,
            provisional.value,
        )
        return Classification(
            state=provisional,
            recognised=False,
            error_code=None,
            error_type=error_type,
            detail=(
                f"error object carrying no code; {provisional.value} is provisional, "
                f"chosen from error_type {error_type!r}"
            ),
        )

    if error_code in _NO_TRANSITION_CODES:
        return Classification(
            state=None,
            recognised=True,
            error_code=error_code,
            error_type=error_type,
            detail=(
                "recognised advance warning; not a state transition — the Item is not "
                "in an error state (section 8.2, F8)"
            ),
        )

    state = _CODE_STATES.get(error_code)
    if state is not None:
        return Classification(
            state=state,
            recognised=True,
            error_code=error_code,
            error_type=error_type,
            detail=f"{error_code} maps to {state.value} (section 8.2)",
        )

    provisional = _TYPE_STATES.get(error_type or "", _UNKNOWN_TYPE_STATE)
    # Loud, and loud without the error_message: the code and type are enough to
    # look it up, and the message is the field that can carry account detail.
    logger.warning(
        "unrecognised Plaid error code %r (type %r); treating the Item as %s provisionally",
        error_code,
        error_type,
        provisional.value,
    )
    return Classification(
        state=provisional,
        recognised=False,
        error_code=error_code,
        error_type=error_type,
        detail=(
            f"unrecognised code {error_code!r}; {provisional.value} is provisional, "
            f"chosen from error_type {error_type!r}"
        ),
    )


def transport_failure(detail: str) -> Classification:
    """No response arrived at all — a timeout, a reset, DNS.

    Section 8.2 puts transport-level errors in ``DEGRADED``, and this one is
    recognised: we know exactly what happened. It says nothing about the Item,
    which is why it must never be allowed to look like evidence of health.
    """
    return Classification(
        state=ItemState.DEGRADED,
        recognised=True,
        error_code=None,
        error_type=None,
        detail=f"transport-level failure, no Plaid response: {detail}",
    )
