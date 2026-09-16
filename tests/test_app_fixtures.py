"""The app's fixtures must keep the shape the publisher actually emits.

Task 21 gives the Flutter app a fixture-backed seam so it does not wait on task
20's HTTP route.  That seam buys independence at one cost: nothing forces the
bundled JSON to keep resembling a real publication, and a fixture that has
drifted still makes every widget test pass.  The app would then be verified
against a payload the daemon never sends.

So the fixtures are compared against a genuine publication built by the real
`Publisher`, decrypted, and diffed key by key.  Editing `publisher._plaintext`
without editing the fixtures fails here, which is the only place the two
languages can be held to one contract.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from networth.model.figure import SnapshotAgeState
from networth.model.staleness import DisplayState
from networth.publisher import SCHEMA_VERSION
from networth.storage import migrate
from tests.test_publisher import NOW, _document, _publisher, _setup_snapshot

APP_FIXTURES = Path(__file__).resolve().parent.parent / "app" / "assets" / "fixtures"


def _fixture(name: str) -> dict[str, Any]:
    body = json.loads((APP_FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(body, dict)
    return body


FIXTURE_NAMES = ("known.json", "mixed_known_and_unknown.json", "static_only.json")


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def published(db: sqlite3.Connection) -> dict[str, Any]:
    """One real publication, decrypted — the shape of record."""

    _setup_snapshot(db)
    return _document(_publisher(db).publish(at=NOW).envelope)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_app_fixture_exists_and_is_an_object(name: str) -> None:
    assert _fixture(name)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_top_level_keys_match_a_real_publication(name: str, published: dict[str, Any]) -> None:
    assert _fixture(name).keys() == published.keys()


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_total_keys_match_a_real_publication(name: str, published: dict[str, Any]) -> None:
    assert _fixture(name)["total"].keys() == published["total"].keys()


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_account_keys_match_a_real_publication(name: str, published: dict[str, Any]) -> None:
    expected = published["accounts"][0]
    accounts = _fixture(name)["accounts"]
    assert accounts, f"{name} carries no accounts to render"
    for account in accounts:
        assert account.keys() == expected.keys()
        assert account["freshness"].keys() == expected["freshness"].keys()


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_item_budget_keys_match_a_real_publication(name: str, published: dict[str, Any]) -> None:
    assert _fixture(name)["item_budget"].keys() == published["item_budget"].keys()


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_enumerated_values_are_ones_this_project_defines(name: str) -> None:
    """A plausible-looking fixture may still name a state that does not exist."""

    body = _fixture(name)
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["total"]["age_state"] in {state.value for state in SnapshotAgeState}
    assert body["connection_state"] in {state.value for state in DisplayState}


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_the_age_state_agrees_with_its_date_field(name: str) -> None:
    """Section 8.1 R3's table, asserted on the data the app is tested against.

    A fixture that disagreed with the table would be testing the app against a
    payload the daemon cannot produce — and the app refuses exactly this
    combination at parse time, so the fixture would be unreachable besides.
    """

    total = _fixture(name)["total"]
    if total["age_state"] == SnapshotAgeState.KNOWN.value:
        assert total["as_of"] is not None
    else:
        assert total["as_of"] is None


def test_the_three_age_states_are_each_covered_exactly_once() -> None:
    """The acceptance criterion is that all three render, so all three must exist."""

    states = [_fixture(name)["total"]["age_state"] for name in FIXTURE_NAMES]
    assert sorted(states) == sorted(state.value for state in SnapshotAgeState)


def test_the_mixed_fixture_is_the_trap_it_claims_to_be() -> None:
    """It must carry a date that a careless implementation would print.

    If this ever became null the widget test asserting "no date near the
    headline" would still pass, while having stopped testing anything — the
    quiet way a regression test turns into decoration.
    """

    total = _fixture("mixed_known_and_unknown.json")["total"]
    assert total["age_state"] == SnapshotAgeState.UNKNOWN.value
    assert total["as_of"] is None
    assert total["oldest_known_source_as_of"] is not None
    assert total["unknown_freshness_account_count"] >= 1
    assert total["account_count"] > total["unknown_freshness_account_count"]
