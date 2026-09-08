"""The observer, on its own: presence, type, and the difference between them.

These were part of the rehearsal suite until the SDK calls moved onto
``PlaidClient`` (§5). They belong here because the distinction they pin is not a
rehearsal detail — §8.1's whole age model rests on it.
"""

from __future__ import annotations

from types import SimpleNamespace

from networth.plaid.observation import FieldObservation, observe, record_set


def test_an_absent_field_and_a_null_field_are_different_observations() -> None:
    """§8.1 turns on this difference, so the observer must not collapse it.

    Absent means Plaid does not supply the field at all — a design constraint.
    Null means Plaid supplies it and has no value — an UNKNOWN age. A report that
    said 'no clock' for both would answer the empirical question wrongly in the
    direction that looks fine.
    """
    records = [SimpleNamespace(present_and_null=None)]

    observed = {o.path: o for o in observe(records, ("present_and_null", "not_there"))}

    assert observed["present_and_null"] == FieldObservation("present_and_null", True, None)
    assert observed["present_and_null"].note == "null"
    assert observed["not_there"] == FieldObservation("not_there", False, None)
    assert observed["not_there"].note == "absent"


def test_a_field_type_is_taken_from_the_first_record_that_supplied_one() -> None:
    records = [SimpleNamespace(price=None), SimpleNamespace(price=1.5)]

    (observed,) = observe(records, ("price",))

    assert observed.present is True
    assert observed.type_name == "float"


def test_a_dotted_path_walks_nested_records() -> None:
    records = [SimpleNamespace(balances=SimpleNamespace(current=1000.0))]

    (observed,) = observe(records, ("balances.current",))

    assert (observed.present, observed.type_name) == (True, "float")


def test_a_path_through_a_null_parent_is_absent_not_a_crash() -> None:
    """A missing balances block must not take the whole observation down with it."""
    records = [SimpleNamespace(balances=None)]

    (observed,) = observe(records, ("balances.current",))

    assert observed.note == "absent"


def test_a_dict_shaped_record_is_read_without_inventing_a_value() -> None:
    records = [{"account_id": "a", "balances": {"current": None}}]

    observed = {o.path: o for o in observe(records, ("account_id", "balances.current", "gone"))}

    assert observed["account_id"].type_name == "str"
    assert observed["balances.current"].note == "null"
    assert observed["gone"].note == "absent"


def test_an_empty_record_set_reports_every_field_absent_and_says_it_saw_nothing() -> None:
    """Count is the guard against reading 'all absent' as 'Plaid supplies nothing'."""
    observed = record_set("accounts", [], ("account_id", "name"))

    assert observed.count == 0
    assert [field.note for field in observed.fields] == ["absent", "absent"]


def test_no_observation_ever_carries_a_value() -> None:
    """The type is the deliverable; the figure is what must never be written down."""
    records = [SimpleNamespace(secret_amount=1234.56, label="brokerage-1")]

    observed = record_set("accounts", records, ("secret_amount", "label"))

    rendered = repr(observed)
    assert "1234.56" not in rendered
    assert "brokerage-1" not in rendered
    assert "float" in rendered and "str" in rendered
