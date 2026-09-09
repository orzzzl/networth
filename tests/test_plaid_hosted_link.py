"""The Hosted Link surface, offline (task ``06a``, plan step 1).

No live call, per task 05's rule. What is under test here is narrower than
"the two methods work": **task 06a's F7 criterion 2 says the poller's "not
ready" branch must run against the real API rather than a guessed fixture**, and
the only way an offline suite can contribute to that is by making the shapes
*distinguishable* — so that when the live run happens, one of them is confirmed
and the others are ruled out. A wrapper that collapsed all of them into
``ready``/``not ready`` would make the live measurement unable to fail, which is
the failure mode this project keeps finding in its own evidence.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model_utils import model_to_dict

from networth.plaid.client import (
    HostedLinkToken,
    LinkSessionShape,
    PlaidCallError,
    PlaidClient,
)
from networth.plaid.environment import PlaidCredentials, PlaidEnvironment
from tests.fake_plaid import (
    HOSTED_LINK_URL,
    LINK_SESSION_FINISHED,
    LINK_SESSION_ID,
    LINK_SESSION_STARTED,
    LINK_TOKEN,
    LINK_TOKEN_EXPIRATION,
    PUBLIC_TOKEN,
    FakeSandboxApi,
    completed_session,
    link_sessions_response,
    session_in_progress,
    session_without_item,
)

CREDENTIALS = PlaidCredentials(
    client_id="synthetic-client",
    secret="synthetic-secret",
    environment=PlaidEnvironment.SANDBOX,
)


def _client(**overrides: object) -> tuple[PlaidClient, FakeSandboxApi]:
    api = FakeSandboxApi(**overrides)
    return PlaidClient(CREDENTIALS, api=api), api


def _create(client: PlaidClient, **kwargs: Any) -> HostedLinkToken:
    defaults: dict[str, Any] = {
        "client_user_id": "synthetic-user",
        "client_name": "networth",
        "products": ["investments"],
        "country_codes": ["US"],
        "language": "en",
    }
    defaults.update(kwargs)
    return client.link_token_create_hosted(**defaults)


def test_hosted_link_is_requested_as_hosted_in_the_serialised_request() -> None:
    """The assertion is on the JSON Plaid sees, not on an attribute read back.

    ``first_institution_supporting`` shipped an HTTP 400 that every offline test
    passed, because ``additional_properties_type`` let the SDK accept an
    undeclared keyword, serialise it at the wrong level, and then hand it back
    to the test that had just set it. An assertion that round-trips through a
    permissive model cannot fail. So this reads the serialised form.
    """
    client, api = _client()
    _create(client, url_lifetime_seconds=1800)

    request = api.request_for("link_token_create")
    assert isinstance(request, LinkTokenCreateRequest)
    body = model_to_dict(request, serialize=True)

    assert body["hosted_link"] == {"url_lifetime_seconds": 1800}
    assert "hosted_link" not in body.get("user", {})
    assert body["user"] == {"client_user_id": "synthetic-user"}
    assert body["products"] == ["investments"]
    assert body["country_codes"] == ["US"]


def test_an_unrequested_url_lifetime_is_absent_from_the_request() -> None:
    """Not asking is not the same as asking for Plaid's default, and the
    request must not claim a number this program never chose."""
    client, api = _client()
    _create(client)

    body = model_to_dict(api.request_for("link_token_create"), serialize=True)
    assert body["hosted_link"] == {}


def test_the_two_clocks_are_returned_separately() -> None:
    """Issue #3: the link token's expiry and the hosted URL's lifetime are
    different lengths that run out independently. A single "deadline" would be a
    guess dressed as a measurement — and task ``06a`` (i) exists to measure the
    second one, which it cannot do if the wrapper has already conflated them."""
    client, _ = _client()
    token = _create(client, url_lifetime_seconds=1800)

    assert token.expires_at == LINK_TOKEN_EXPIRATION
    assert token.url_lifetime_seconds == 1800


def test_an_unrequested_url_lifetime_is_recorded_as_unknown_not_as_a_number() -> None:
    client, _ = _client()
    token = _create(client)

    assert token.url_lifetime_seconds is None


def test_a_naive_expiration_is_a_failed_call_not_a_missing_expiry() -> None:
    """``None`` is kept meaning exactly one thing: Plaid reported no expiry.
    Coercing a naive datetime would invent a deadline, and the sibling project
    already shipped a "no notification" bug that came down to that assumption."""
    client, _ = _client(
        link_token_create=SimpleNamespace(
            link_token=LINK_TOKEN,
            hosted_link_url=HOSTED_LINK_URL,
            expiration=datetime(2026, 9, 8, 21, 0),  # noqa: DTZ001 — the point of the test
        )
    )
    with pytest.raises(PlaidCallError, match="aware datetime"):
        _create(client)


def test_an_absent_expiration_is_none_rather_than_an_error() -> None:
    client, _ = _client(
        link_token_create=SimpleNamespace(link_token=LINK_TOKEN, hosted_link_url=HOSTED_LINK_URL)
    )
    assert _create(client).expires_at is None


def test_a_link_token_with_no_hosted_url_is_refused() -> None:
    """Hosted Link has no frontend integration to fall back to, so a session
    with no URL is one nobody can open. Refused rather than returned half-built."""
    client, _ = _client(link_token_create=SimpleNamespace(link_token=LINK_TOKEN))
    with pytest.raises(PlaidCallError, match="hosted_link_url"):
        _create(client)


def test_a_response_with_no_link_token_is_refused() -> None:
    client, _ = _client(link_token_create=SimpleNamespace(hosted_link_url=HOSTED_LINK_URL))
    with pytest.raises(PlaidCallError, match="no link_token"):
        _create(client)


def test_the_hosted_token_redacts_both_strings() -> None:
    """The hosted URL is openable by whoever holds it, and completing a Link
    through it spends one of the ten lifetime slots (F2a). That makes it a
    spendable thing, not a diagnostic to print into a log."""
    client, _ = _client()
    rendered = repr(_create(client, url_lifetime_seconds=1800))

    assert LINK_TOKEN not in rendered
    assert HOSTED_LINK_URL not in rendered
    assert "<redacted>" in rendered
    assert "1800" in rendered


# --- /link/token/get: the negative shapes, the positive one, and the unsafe one


def test_before_completion_the_key_is_absent_and_that_is_its_own_shape() -> None:
    """**F7 criterion 2.** The fake's default omits ``link_sessions`` entirely,
    which is what the live Sandbox run has to confirm. Asserting an empty list
    instead would be the guessed fixture the criterion forbids."""
    client, _ = _client()
    poll = client.link_token_get(LINK_TOKEN)

    assert poll.shape is LinkSessionShape.SESSIONS_ABSENT
    assert poll.public_tokens == ()
    assert not poll.item_added


def test_an_explicit_null_is_not_the_same_shape_as_an_absent_key() -> None:
    """Absent means the reply was not the shape we understand; null means Plaid
    affirmatively reported nothing. ``item_get`` draws the same line for
    ``item.error``, and folding them together is what would make the live
    measurement unable to fail."""
    client, _ = _client(link_token_get=link_sessions_response(sessions=None))
    assert client.link_token_get(LINK_TOKEN).shape is LinkSessionShape.SESSIONS_NULL


def test_an_empty_list_is_its_own_shape_too() -> None:
    client, _ = _client(link_token_get=link_sessions_response(sessions=[]))
    assert client.link_token_get(LINK_TOKEN).shape is LinkSessionShape.NO_SESSIONS


def test_a_concluded_session_that_added_nothing_is_its_own_shape() -> None:
    """An exit. Distinct from "no sessions" because it is the shape task ``06a``
    (ii)/(iii) will meet after an abandoned attempt — and **F2a** says an
    abandoned session spends no slot, so the difference is load-bearing for the
    Item budget, not cosmetic."""
    client, _ = _client(link_token_get=link_sessions_response(sessions=[session_without_item()]))
    poll = client.link_token_get(LINK_TOKEN)

    assert poll.shape is LinkSessionShape.NO_ITEM_ADDED
    assert poll.public_tokens == ()
    assert poll.session_ids == (LINK_SESSION_ID,)


def test_a_session_still_open_is_not_the_same_shape_as_one_that_exited() -> None:
    """PR #58 review, finding 2. ``finished_at`` is ``datetime | None`` in the
    SDK, so "the owner is inside Link right now" and "the owner gave up" are
    distinguishable in the reply — and they are the two states §7 / ``07a``
    transition on differently. Reporting both as ``NO_ITEM_ADDED`` would leave
    the state machine nothing to transition on."""
    client, _ = _client(link_token_get=link_sessions_response(sessions=[session_in_progress()]))
    poll = client.link_token_get(LINK_TOKEN)

    assert poll.shape is LinkSessionShape.SESSION_IN_PROGRESS
    assert not poll.item_added
    assert poll.sessions[0].started_at == LINK_SESSION_STARTED
    assert poll.sessions[0].finished_at is None
    assert not poll.sessions[0].finished


def test_one_session_still_open_keeps_the_whole_reply_in_progress() -> None:
    """A concluded session next to a live one must not read as "everything is
    over": the live one is the reason to keep polling."""
    client, _ = _client(
        link_token_get=link_sessions_response(
            sessions=[
                session_without_item(session_id="exited-session"),
                session_in_progress(session_id="live-session"),
            ]
        )
    )
    assert client.link_token_get(LINK_TOKEN).shape is LinkSessionShape.SESSION_IN_PROGRESS


def test_a_completed_session_yields_its_public_token_and_both_instants() -> None:
    client, _ = _client(link_token_get=link_sessions_response(sessions=[completed_session()]))
    poll = client.link_token_get(LINK_TOKEN)

    assert poll.shape is LinkSessionShape.ITEM_ADDED
    assert poll.item_added
    assert poll.public_tokens == (PUBLIC_TOKEN,)
    assert poll.session_ids == (LINK_SESSION_ID,)
    assert poll.sessions[0].started_at == LINK_SESSION_STARTED
    assert poll.sessions[0].finished_at == LINK_SESSION_FINISHED


def test_an_item_added_with_no_token_is_not_reported_as_nothing_added() -> None:
    """PR #58 review, finding 2, and the unsafe half of it. An Item-add result
    with no ``public_token`` means a lifetime slot is spent (**F2a**) and we hold
    no handle to it. The first cut counted tokens only, so it reported that as
    ``NO_ITEM_ADDED`` — "nothing happened" about something permanent."""
    client, _ = _client(
        link_token_get=link_sessions_response(
            sessions=[completed_session(public_tokens=(), untokened_results=1)]
        )
    )
    poll = client.link_token_get(LINK_TOKEN)

    assert poll.shape is LinkSessionShape.ITEM_ADDED_TOKEN_ABSENT
    assert poll.item_added
    assert poll.public_tokens == ()
    assert poll.tokens_missing == 1


def test_a_missing_token_outranks_the_tokens_that_are_present() -> None:
    """The shape names what a reader must not miss. A reply that added two Items
    and showed one token is not ``ITEM_ADDED``: acting on it as if it were would
    exchange one slot and forget the other for good."""
    client, _ = _client(
        link_token_get=link_sessions_response(
            sessions=[completed_session(public_tokens=("public-a",), untokened_results=1)]
        )
    )
    poll = client.link_token_get(LINK_TOKEN)

    assert poll.shape is LinkSessionShape.ITEM_ADDED_TOKEN_ABSENT
    assert poll.public_tokens == ("public-a",)
    assert poll.tokens_missing == 1


def test_every_public_token_is_returned_because_each_one_is_a_spent_slot() -> None:
    """One reply can describe several sessions, and multi-Item Link can add
    several Items in one. Each token is a slot already spent (**F2a**), so
    returning the first and dropping the rest would silently lose Items the
    owner has already paid for — in the direction that cannot be undone."""
    client, _ = _client(
        link_token_get=link_sessions_response(
            sessions=[
                completed_session(public_tokens=["public-a", "public-b"]),
                session_without_item(session_id="other-session"),
            ]
        )
    )
    poll = client.link_token_get(LINK_TOKEN)

    assert poll.shape is LinkSessionShape.ITEM_ADDED
    assert poll.public_tokens == ("public-a", "public-b")
    assert poll.session_ids == (LINK_SESSION_ID, "other-session")


def test_each_token_stays_with_the_session_that_spent_the_slot() -> None:
    """PR #58 review, finding 2. Parallel tuples of ids and tokens cannot say
    which attempt spent which slot — they are not even the same length here."""
    client, _ = _client(
        link_token_get=link_sessions_response(
            sessions=[
                session_without_item(session_id="exited-session"),
                completed_session(session_id="paid-session", public_tokens=["public-a"]),
            ]
        )
    )
    poll = client.link_token_get(LINK_TOKEN)

    assert [(s.session_id, s.public_tokens) for s in poll.sessions] == [
        ("exited-session", ()),
        ("paid-session", ("public-a",)),
    ]


def test_a_link_sessions_that_is_not_a_list_is_a_failed_call() -> None:
    client, _ = _client(link_token_get=link_sessions_response(sessions={"nope": True}))
    with pytest.raises(PlaidCallError, match="not a list"):
        client.link_token_get(LINK_TOKEN)


def test_a_naive_session_instant_is_a_failed_call_not_a_coerced_one() -> None:
    """Measurement (i) subtracts these two instants. A naive value assumed to be
    UTC does not look like an error, it looks like a URL lifetime hours off —
    the same reasoning ``expiration`` already carries, one level in."""
    client, _ = _client(
        link_token_get=link_sessions_response(
            sessions=[
                session_without_item(
                    finished_at=datetime(2026, 9, 8, 20, 4),  # noqa: DTZ001 — the point of the test
                )
            ]
        )
    )
    with pytest.raises(PlaidCallError, match="finished_at that is not an aware datetime"):
        client.link_token_get(LINK_TOKEN)


def test_a_session_with_no_instants_is_read_as_still_open() -> None:
    """The safe direction: calling a live session concluded lets a poller stop
    while the owner is still inside Link, and Hosted Link produces no other
    signal that would correct it."""
    client, _ = _client(
        link_token_get=link_sessions_response(
            sessions=[session_without_item(started_at=None, finished_at=None)]
        )
    )
    poll = client.link_token_get(LINK_TOKEN)

    assert poll.shape is LinkSessionShape.SESSION_IN_PROGRESS
    assert poll.sessions[0].started_at is None


def test_the_poll_redacts_tokens_and_session_ids() -> None:
    """A ``public_token`` is a bearer credential for an already-spent slot, and
    the session ids are Plaid's handles for the owner's own Link attempts."""
    client, _ = _client(link_token_get=link_sessions_response(sessions=[completed_session()]))
    poll = client.link_token_get(LINK_TOKEN)

    for rendered in (repr(poll), repr(poll.sessions[0])):
        assert PUBLIC_TOKEN not in rendered
        assert LINK_SESSION_ID not in rendered
    assert "ITEM_ADDED" in repr(poll)


def test_the_get_request_carries_the_link_token() -> None:
    client, api = _client()
    client.link_token_get(LINK_TOKEN)

    body = model_to_dict(api.request_for("link_token_get"), serialize=True)
    assert body["link_token"] == LINK_TOKEN
