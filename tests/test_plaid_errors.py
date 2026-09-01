"""The taxonomy is the thing that decides whether the owner is told his bank
connection is dead, so these tests are about the two ways it could lie: calling
something healthy that is not, and asking for action when none is warranted."""

from __future__ import annotations

import logging

import pytest
from plaid.model.plaid_error_type import PlaidErrorType

from networth.plaid.errors import (
    _CODE_STATES,
    _NO_TRANSITION_CODES,
    _TYPE_STATES,
    Classification,
    ItemState,
    classify,
    transport_failure,
)

# The four the task entry requires, with the state each must reach.
REQUIRED = {
    "ITEM_LOGIN_REQUIRED": ItemState.NEEDS_REAUTH,
    "PENDING_EXPIRATION": ItemState.NEEDS_REAUTH,
    "USER_PERMISSION_REVOKED": ItemState.REVOKED,
    "ITEM_NOT_FOUND": ItemState.REVOKED,
}


@pytest.mark.parametrize(("code", "expected"), sorted(REQUIRED.items()))
def test_required_codes_map_to_exactly_one_state(code: str, expected: ItemState) -> None:
    result = classify(code, "ITEM_ERROR")
    assert result.state is expected
    assert result.recognised


def test_every_required_code_is_covered() -> None:
    """Named separately from the parametrised test above: that one proves each
    mapping, this one proves none was dropped from the table."""
    assert REQUIRED.keys() <= _CODE_STATES.keys()


def test_reauth_and_revoked_are_owner_actionable_and_degraded_is_not() -> None:
    """Section 8.2: confusing "the institution is down" with "your connection is
    dead" is where alert fatigue starts."""
    assert classify("ITEM_LOGIN_REQUIRED", "ITEM_ERROR").owner_actionable
    assert classify("ITEM_NOT_FOUND", "ITEM_ERROR").owner_actionable
    assert not classify("SOMETHING_NEW", "INSTITUTION_ERROR").owner_actionable
    assert not classify(None, None).owner_actionable


def test_only_a_clean_response_is_healthy() -> None:
    assert classify(None, None).state is ItemState.HEALTHY
    for kind in PlaidErrorType.allowed_values[("value",)]:
        result = classify("A_CODE_WE_HAVE_NEVER_SEEN", kind)
        assert result.state is not ItemState.HEALTHY, kind
        assert not result.recognised, kind


def test_unknown_code_is_loud(caplog: pytest.LogCaptureFixture) -> None:
    """ "Loud" has to mean something a person can find. The flag is on the result
    and the code is in a warning; both, because the flag is only read by code
    that already knows to look."""
    with caplog.at_level(logging.WARNING, logger="networth.plaid.errors"):
        result = classify("BRAND_NEW_CODE", "ITEM_ERROR")
    assert not result.recognised
    assert result.state is ItemState.DEGRADED
    assert "BRAND_NEW_CODE" in caplog.text


def test_unknown_item_error_does_not_ask_the_owner_for_anything() -> None:
    """It would be easy to argue an unknown ITEM_ERROR should be NEEDS_REAUTH —
    it is an Item problem after all. It must not be: we do not know he can fix
    it, and the false alert is the one he learns to ignore before a true one."""
    result = classify("UNKNOWN_ITEM_PROBLEM", "ITEM_ERROR")
    assert result.state is ItemState.DEGRADED
    assert not result.owner_actionable
    assert not result.recognised


def test_unknown_type_is_still_not_healthy() -> None:
    """A type the SDK does not enumerate either — the case where both halves of
    the input are new."""
    result = classify("WHAT_IS_THIS", "A_TYPE_FROM_THE_FUTURE")
    assert result.state is ItemState.DEGRADED
    assert not result.recognised


def test_pending_disconnect_is_recognised_and_is_not_a_transition() -> None:
    """F8 and section 8.2: an Item scheduled for migration is not in an error
    state. Mapping it to NEEDS_REAUTH would render an advance warning as a live
    outage, and mapping it to HEALTHY would throw the warning away."""
    result = classify("PENDING_DISCONNECT", "ITEM_ERROR")
    assert result.state is None
    assert result.recognised
    assert not result.owner_actionable
    assert "PENDING_DISCONNECT" in _NO_TRANSITION_CODES


def test_transport_failure_is_degraded_and_recognised() -> None:
    result = transport_failure("connection reset")
    assert result.state is ItemState.DEGRADED
    assert result.recognised
    assert result.error_code is None
    assert "connection reset" in result.detail


def test_transport_failure_is_not_confusable_with_health() -> None:
    """It has no error_code, exactly like a clean response. Anything reading
    health off the code rather than off the state gets this backwards."""
    assert transport_failure("timeout").state is not classify(None, None).state


def test_type_table_names_only_types_the_sdk_knows() -> None:
    """The type table is checkable against the SDK, so it is checked rather than
    trusted. The *code* table cannot be — Plaid does not enumerate codes here —
    which is why it holds only codes DESIGN.md itself names."""
    known = set(PlaidErrorType.allowed_values[("value",)])
    assert set(_TYPE_STATES) <= known


def test_classification_is_frozen() -> None:
    result = classify("ITEM_LOGIN_REQUIRED", "ITEM_ERROR")
    assert isinstance(result, Classification)
    with pytest.raises(AttributeError):
        result.state = ItemState.HEALTHY  # type: ignore[misc]


def test_no_code_maps_to_healthy() -> None:
    """The property that keeps a failure from rendering as a live connection:
    HEALTHY is unreachable from any error input at all."""
    for code, state in _CODE_STATES.items():
        assert state is not ItemState.HEALTHY, code
    for kind, state in _TYPE_STATES.items():
        assert state is not ItemState.HEALTHY, kind
