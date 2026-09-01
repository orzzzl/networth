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
    HEALTHY,
    Classification,
    ItemState,
    classify_error,
    malformed_response,
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
    result = classify_error(code, "ITEM_ERROR")
    assert result.state is expected
    assert result.recognised


def test_every_required_code_is_covered() -> None:
    """Named separately from the parametrised test above: that one proves each
    mapping, this one proves none was dropped from the table."""
    assert REQUIRED.keys() <= _CODE_STATES.keys()


def test_reauth_and_revoked_are_owner_actionable_and_degraded_is_not() -> None:
    """Section 8.2: confusing "the institution is down" with "your connection is
    dead" is where alert fatigue starts."""
    assert classify_error("ITEM_LOGIN_REQUIRED", "ITEM_ERROR").owner_actionable
    assert classify_error("ITEM_NOT_FOUND", "ITEM_ERROR").owner_actionable
    assert not classify_error("SOMETHING_NEW", "INSTITUTION_ERROR").owner_actionable


def test_no_input_at_all_reaches_healthy() -> None:
    """`classify_error` is called only when an error object exists, so there is
    no argument pair that may come back healthy — including the pair that used
    to: `(None, anything)`. Health is the caller's fact to establish, not this
    function's default."""
    for kind in [None, *PlaidErrorType.allowed_values[("value",)], "A_TYPE_FROM_THE_FUTURE"]:
        for code in (None, "", "A_CODE_WE_HAVE_NEVER_SEEN", *REQUIRED, *_NO_TRANSITION_CODES):
            result = classify_error(code, kind)
            assert result.state is not ItemState.HEALTHY, (code, kind)


def test_an_error_with_a_type_but_no_code_is_not_healthy() -> None:
    """The round-1 blocker, at this layer. Plaid's error object carries several
    fields and nothing guarantees a code is among them; reading "no code" as "no
    error" turned a real ITEM_ERROR into a live connection."""
    result = classify_error(None, "ITEM_ERROR")
    assert result.state is ItemState.DEGRADED
    assert not result.recognised
    assert result.error_type == "ITEM_ERROR"


def test_an_error_carrying_neither_code_nor_type_is_not_healthy() -> None:
    """The emptiest error object Plaid could hand us is still an error object."""
    result = classify_error(None, None)
    assert result.state is ItemState.DEGRADED
    assert not result.recognised
    assert not result.owner_actionable


def test_a_codeless_error_of_a_known_type_takes_that_type_s_state() -> None:
    """It is unrecognised, but not uninformative: an INSTITUTION_ERROR with no
    code is still the institution's problem and still must not page the owner."""
    result = classify_error(None, "INSTITUTION_ERROR")
    assert result.state is ItemState.DEGRADED
    assert not result.owner_actionable
    assert not result.recognised


def test_a_codeless_error_is_loud(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="networth.plaid.errors"):
        classify_error(None, "ITEM_ERROR")
    assert "no error_code" in caplog.text


def test_malformed_response_is_degraded_and_unrecognised() -> None:
    """An unreadable answer is not evidence of health, and it is not an ordinary
    bank outage either — `recognised` False is what separates the two."""
    result = malformed_response("the response carried no item")
    assert result.state is ItemState.DEGRADED
    assert not result.recognised
    assert not result.owner_actionable
    assert "no item" in result.detail


def test_unknown_code_is_loud(caplog: pytest.LogCaptureFixture) -> None:
    """ "Loud" has to mean something a person can find. The flag is on the result
    and the code is in a warning; both, because the flag is only read by code
    that already knows to look."""
    with caplog.at_level(logging.WARNING, logger="networth.plaid.errors"):
        result = classify_error("BRAND_NEW_CODE", "ITEM_ERROR")
    assert not result.recognised
    assert result.state is ItemState.DEGRADED
    assert "BRAND_NEW_CODE" in caplog.text


def test_unknown_item_error_does_not_ask_the_owner_for_anything() -> None:
    """It would be easy to argue an unknown ITEM_ERROR should be NEEDS_REAUTH —
    it is an Item problem after all. It must not be: we do not know he can fix
    it, and the false alert is the one he learns to ignore before a true one."""
    result = classify_error("UNKNOWN_ITEM_PROBLEM", "ITEM_ERROR")
    assert result.state is ItemState.DEGRADED
    assert not result.owner_actionable
    assert not result.recognised


def test_unknown_type_is_still_not_healthy() -> None:
    """A type the SDK does not enumerate either — the case where both halves of
    the input are new."""
    result = classify_error("WHAT_IS_THIS", "A_TYPE_FROM_THE_FUTURE")
    assert result.state is ItemState.DEGRADED
    assert not result.recognised


def test_pending_disconnect_is_recognised_and_is_not_a_transition() -> None:
    """F8 and section 8.2: an Item scheduled for migration is not in an error
    state. Mapping it to NEEDS_REAUTH would render an advance warning as a live
    outage, and mapping it to HEALTHY would throw the warning away."""
    result = classify_error("PENDING_DISCONNECT", "ITEM_ERROR")
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
    assert transport_failure("timeout").state is not ItemState.HEALTHY
    assert transport_failure("timeout").error_code is HEALTHY.error_code


def test_type_table_names_only_types_the_sdk_knows() -> None:
    """The type table is checkable against the SDK, so it is checked rather than
    trusted. The *code* table cannot be — Plaid does not enumerate codes here —
    which is why it holds only codes DESIGN.md itself names."""
    known = set(PlaidErrorType.allowed_values[("value",)])
    assert set(_TYPE_STATES) <= known


def test_classification_is_frozen() -> None:
    result = classify_error("ITEM_LOGIN_REQUIRED", "ITEM_ERROR")
    assert isinstance(result, Classification)
    with pytest.raises(AttributeError):
        result.state = ItemState.HEALTHY  # type: ignore[misc]


def test_no_table_entry_maps_to_healthy() -> None:
    """The property that keeps a failure from rendering as a live connection:
    HEALTHY is unreachable from any error input at all."""
    for code, state in _CODE_STATES.items():
        assert state is not ItemState.HEALTHY, code
    for kind, state in _TYPE_STATES.items():
        assert state is not ItemState.HEALTHY, kind
