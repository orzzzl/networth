"""Task 13: the quote's own ``as_of`` is the source clock, and nothing else is."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from networth.config import ConfigError
from networth.model.manual import QUOTE_AS_OF, EquityHolding
from networth.quotes import (
    QUOTES_URL,
    QuoteClient,
    QuoteCredentials,
    QuoteUnavailable,
    configured_feed,
    load_quote_credentials,
)

KEY_ID = "synthetic-key-id"
SECRET_KEY = "synthetic-secret-key"

#: The venue's timestamp for the price, and deliberately not "recently": every
#: assertion that this instant survives is an assertion that no code path
#: substituted the moment of the request.
QUOTE_TIME = datetime(2026, 1, 14, 21, 0, tzinfo=UTC)


class FakeResponse:
    def __init__(self, status: int, data: bytes) -> None:
        self.status = status
        self.data = data


class FakeTransport:
    """Records the request and answers with a canned response or an error."""

    def __init__(
        self,
        *,
        status: int = 200,
        payload: object | None = None,
        body: bytes | None = None,
        error: Exception | None = None,
    ) -> None:
        if body is None:
            body = b"" if payload is None else json.dumps(payload).encode()
        self._status = status
        self._body = body
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        fields: Any,
        headers: Any,
        timeout: float,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "fields": dict(fields),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if self._error is not None:
            raise self._error
        return FakeResponse(self._status, self._body)


def entry(bid: object, ask: object, stamp: object) -> dict[str, object]:
    return {"bp": bid, "ap": ask, "t": stamp}


def payload(**quotes: object) -> dict[str, object]:
    return {"quotes": dict(quotes)}


def client_for(transport: FakeTransport, *, feed: str | None = None) -> QuoteClient:
    return QuoteClient(
        QuoteCredentials(key_id=KEY_ID, secret_key=SECRET_KEY),
        transport=transport,
        feed=feed,
    )


def write_quotes_env(directory: Path, text: str) -> Path:
    directory.joinpath("quotes.env").write_text(text, encoding="utf-8")
    return directory


# --- the acceptance criterion ------------------------------------------------


def test_the_quotes_own_timestamp_is_the_source_clock_not_the_request_time() -> None:
    transport = FakeTransport(payload=payload(SYNTH=entry(100.00, 100.02, "2026-01-14T21:00:00Z")))

    quote = client_for(transport).get_quote("SYNTH")

    assert quote.as_of == QUOTE_TIME
    # The request happened now; the price is dated when the venue dated it, and
    # the gap between the two is the whole point of section 8.1's R1.
    assert datetime.now(UTC) - quote.as_of > timedelta(days=1)


def test_the_source_clock_survives_into_the_valued_holding() -> None:
    transport = FakeTransport(payload=payload(SYNTH=entry(10.00, 10.00, "2026-01-14T21:00:00Z")))
    holding = EquityHolding(
        symbol="SYNTH",
        shares=Decimal("3.5"),
        currency="USD",
        set_on=datetime(2025, 6, 1, tzinfo=UTC),
    )

    figure = holding.value_with(client_for(transport).get_quote("SYNTH"))

    assert figure.value_minor == 3500
    assert figure.as_of == QUOTE_TIME
    assert figure.source_clock == QUOTE_AS_OF


def test_a_quote_without_a_usable_timestamp_is_refused_rather_than_dated_now() -> None:
    undated = [None, "", "not-a-time", "2026-01-14T21:00:00", 1768424400]
    for stamp in undated:
        transport = FakeTransport(payload=payload(SYNTH=entry(100.00, 100.02, stamp)))
        with pytest.raises(QuoteUnavailable):
            client_for(transport).get_quote("SYNTH")


def test_an_offset_timestamp_becomes_the_same_instant_in_utc() -> None:
    transport = FakeTransport(
        payload=payload(SYNTH=entry(100.00, 100.02, "2026-01-14T16:00:00-05:00"))
    )

    quote = client_for(transport).get_quote("SYNTH")

    assert quote.as_of == QUOTE_TIME
    assert quote.as_of.utcoffset() == timedelta(0)


# --- money is never a float --------------------------------------------------


def test_the_price_is_an_exact_decimal_midpoint() -> None:
    transport = FakeTransport(
        body=json.dumps(payload(SYNTH=entry(100.005, 100.015, "2026-01-14T21:00:00Z"))).encode()
    )

    quote = client_for(transport).get_quote("SYNTH")

    assert isinstance(quote.price, Decimal)
    assert quote.price == Decimal("100.01")
    # And the contrast, so this test says something a float would also satisfy:
    # the same midpoint computed the default way is 100.00999999999999431..., a
    # price that multiplied by a share count gives a total nobody can reconcile.
    assert Decimal((100.005 + 100.015) / 2) != Decimal("100.01")


def test_a_non_positive_or_unusable_side_is_not_a_price() -> None:
    for bid, ask in ((0, 100.02), (100.00, 0), (-1, 100.02), ("100.00", 100.02), (None, None)):
        transport = FakeTransport(payload=payload(SYNTH=entry(bid, ask, "2026-01-14T21:00:00Z")))
        with pytest.raises(QuoteUnavailable):
            client_for(transport).get_quote("SYNTH")


# --- the request -------------------------------------------------------------


def test_the_request_carries_the_credentials_and_the_batch_of_symbols() -> None:
    transport = FakeTransport(
        payload=payload(
            ONE=entry(1.00, 1.02, "2026-01-14T21:00:00Z"),
            TWO=entry(2.00, 2.02, "2026-01-14T21:00:00Z"),
        )
    )

    client_for(transport).get_quotes(["one", "TWO"])

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == QUOTES_URL
    assert call["fields"]["symbols"] == "ONE,TWO"
    assert call["headers"]["APCA-API-KEY-ID"] == KEY_ID
    assert call["headers"]["APCA-API-SECRET-KEY"] == SECRET_KEY


def test_the_feed_is_sent_only_when_it_is_configured() -> None:
    unset = FakeTransport(payload=payload(SYNTH=entry(1.00, 1.02, "2026-01-14T21:00:00Z")))
    client_for(unset).get_quote("SYNTH")
    assert "feed" not in unset.calls[0]["fields"]

    configured = FakeTransport(payload=payload(SYNTH=entry(1.00, 1.02, "2026-01-14T21:00:00Z")))
    client_for(configured, feed="synthetic-feed").get_quote("SYNTH")
    assert configured.calls[0]["fields"]["feed"] == "synthetic-feed"


def test_no_symbols_asks_nothing_of_the_venue() -> None:
    transport = FakeTransport(payload=payload())

    assert client_for(transport).get_quotes([]) == {}
    assert transport.calls == []


# --- absence is absence ------------------------------------------------------


def test_a_batch_keeps_the_prices_it_got_and_omits_the_ones_it_did_not() -> None:
    transport = FakeTransport(
        payload=payload(
            ONE=entry(1.00, 1.02, "2026-01-14T21:00:00Z"),
            THREE=entry(3.00, 3.02, "2026-01-14T21:00:00Z"),
        )
    )

    quotes = client_for(transport).get_quotes(["ONE", "TWO", "THREE"])

    assert sorted(quotes) == ["ONE", "THREE"]
    assert "TWO" not in quotes


def test_one_unusable_entry_does_not_cost_the_other_holdings_their_prices() -> None:
    transport = FakeTransport(
        payload=payload(
            ONE=entry(1.00, 1.02, "2026-01-14T21:00:00Z"),
            TWO=entry(2.00, 2.02, None),
            THREE=entry(3.00, 3.02, "2026-01-14T21:00:00Z"),
        )
    )

    quotes = client_for(transport).get_quotes(["ONE", "TWO", "THREE"])

    assert sorted(quotes) == ["ONE", "THREE"]


def test_a_price_for_a_symbol_nobody_asked_about_is_ignored() -> None:
    transport = FakeTransport(payload=payload(OTHER=entry(9.00, 9.02, "2026-01-14T21:00:00Z")))

    assert client_for(transport).get_quotes(["SYNTH"]) == {}
    with pytest.raises(QuoteUnavailable):
        client_for(transport).get_quote("SYNTH")


def test_a_symbol_is_matched_by_the_asset_not_by_its_spelling() -> None:
    transport = FakeTransport(payload=payload(SYNTH=entry(1.00, 1.02, "2026-01-14T21:00:00Z")))

    quotes = client_for(transport).get_quotes(["synth"])

    assert list(quotes) == ["SYNTH"]
    assert quotes["SYNTH"].symbol == "SYNTH"


# --- failures ----------------------------------------------------------------


@pytest.mark.parametrize(
    "transport",
    [
        FakeTransport(status=429),
        FakeTransport(status=500),
        FakeTransport(body=b"<html>not json</html>"),
        FakeTransport(payload=["not", "an", "object"]),
        FakeTransport(payload={"no": "quotes key"}),
        FakeTransport(payload={"quotes": ["not", "an", "object"]}),
        FakeTransport(error=OSError("synthetic transport failure")),
    ],
)
def test_every_unusable_answer_is_one_refusal_and_not_a_price(
    transport: FakeTransport,
) -> None:
    with pytest.raises(QuoteUnavailable):
        client_for(transport).get_quote("SYNTH")


def test_a_transport_failure_does_not_carry_the_request_into_the_message() -> None:
    # A transport exception is free to quote the request it failed on, and this
    # request is authenticated by its headers.
    transport = FakeTransport(error=OSError(f"failed sending {KEY_ID}:{SECRET_KEY}"))

    with pytest.raises(QuoteUnavailable) as raised:
        client_for(transport).get_quote("SYNTH")

    assert KEY_ID not in str(raised.value)
    assert SECRET_KEY not in str(raised.value)
    assert raised.value.__cause__ is None


def test_the_credentials_do_not_render_themselves() -> None:
    credentials = QuoteCredentials(key_id=KEY_ID, secret_key=SECRET_KEY)

    for text in (repr(credentials), str(credentials), f"{credentials}"):
        assert KEY_ID not in text
        assert SECRET_KEY not in text
        assert "redacted" in text


# --- the owner's file --------------------------------------------------------


def test_the_key_pair_is_read_from_the_owners_file(tmp_path: Path) -> None:
    write_quotes_env(tmp_path, f"{'ALPACA_KEY_ID'}={KEY_ID}\nALPACA_SECRET_KEY={SECRET_KEY}\n")

    credentials = load_quote_credentials(secrets_dir=tmp_path)

    assert credentials.key_id == KEY_ID
    assert credentials.secret_key == SECRET_KEY
    assert configured_feed(secrets_dir=tmp_path) is None


def test_a_configured_feed_is_read_and_an_empty_one_is_not(tmp_path: Path) -> None:
    write_quotes_env(
        tmp_path,
        f"ALPACA_KEY_ID={KEY_ID}\nALPACA_SECRET_KEY={SECRET_KEY}\nALPACA_FEED=synthetic-feed\n",
    )
    assert configured_feed(secrets_dir=tmp_path) == "synthetic-feed"

    write_quotes_env(
        tmp_path,
        f"ALPACA_KEY_ID={KEY_ID}\nALPACA_SECRET_KEY={SECRET_KEY}\nALPACA_FEED=\n",
    )
    assert configured_feed(secrets_dir=tmp_path) is None


def test_a_missing_file_says_who_installs_it_and_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as raised:
        load_quote_credentials(secrets_dir=tmp_path)

    message = str(raised.value)
    assert str(tmp_path / "quotes.env") in message
    assert "owner installs it" in message


def test_a_file_missing_a_key_is_refused_without_echoing_the_other(tmp_path: Path) -> None:
    write_quotes_env(tmp_path, f"ALPACA_KEY_ID={KEY_ID}\n")

    with pytest.raises(ConfigError) as raised:
        load_quote_credentials(secrets_dir=tmp_path)

    message = str(raised.value)
    assert "ALPACA_SECRET_KEY" in message
    assert KEY_ID not in message
