"""Locked SDK/urllib3 transport, synthetic loopback and connection failures only."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import urllib3

import networth.plaid.client as wrapper
from networth.link_worker import run_request
from networth.plaid.client import ExchangedItem, PlaidCallError, PlaidClient
from networth.plaid.environment import PlaidEnvironment
from networth.storage import migrate
from networth.tokenstore import SecretKind, TokenStore
from tests.fake_plaid import ACCESS_TOKEN
from tests.test_link_worker import FLOW, LINK, NOW, STAMP, Client
from tests.test_plaid_client import CREDENTIALS


@pytest.fixture
def route(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {"mode": "redirect", "paths": []}
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["paths"].append(self.path)
            if state["mode"] == "redirect" and self.path != "/replayed":
                self.send_response(307)
                self.send_header("Location", "/replayed")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if state["mode"] == "silent":
                # Bounded even if the timeout is deleted in a control mutation.
                release.wait(timeout=1)
            body = json.dumps(
                {
                    "access_token": ACCESS_TOKEN,
                    "item_id": "synthetic-item",
                    "request_id": "syntheticrequest",
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(PlaidEnvironment, "api_host", property(lambda _: host))
    try:
        yield state
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_sdk_does_not_follow_an_exchange_redirect(route: dict[str, Any]) -> None:
    client = PlaidClient(CREDENTIALS)
    with pytest.raises(PlaidCallError, match="HTTP 307"):
        client.item_public_token_exchange("synthetic-public")
    assert route["paths"] == ["/item/public_token/exchange"]


def test_sdk_does_not_retry_a_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def refused(connection: Any) -> Any:
        nonlocal calls
        calls += 1
        raise urllib3.exceptions.NewConnectionError(connection, "synthetic refused connection")

    monkeypatch.setattr(urllib3.connection.HTTPSConnection, "_new_conn", refused)
    with pytest.raises(PlaidCallError):
        PlaidClient(CREDENTIALS).item_public_token_exchange("synthetic-public")
    assert calls == 1


@pytest.mark.parametrize(
    "endpoint, expected",
    [
        ("item_get", (10.0, 120.0)),
        ("fetch_cached_balances", (10.0, 120.0)),
        ("link_token_get", (10.0, 30.0)),
        ("item_public_token_exchange", (10.0, 30.0)),
        ("link_token_create", (10.0, 30.0)),
    ],
)
def test_timeout_reaches_urllib3_through_the_real_sdk(
    monkeypatch: pytest.MonkeyPatch, endpoint: str, expected: tuple[float, float]
) -> None:
    observed: list[tuple[float, float]] = []

    def unavailable(self: Any, method: str, url: str, **kwargs: Any) -> Any:
        timeout = kwargs["timeout"]
        assert isinstance(timeout, urllib3.util.Timeout)
        connect, read = timeout.connect_timeout, timeout.read_timeout
        assert isinstance(connect, float) and isinstance(read, float)
        observed.append((connect, read))
        raise urllib3.exceptions.ReadTimeoutError(
            urllib3.HTTPConnectionPool("synthetic.invalid"), None, "synthetic-private-body"
        )

    monkeypatch.setattr(urllib3.PoolManager, "request", unavailable)
    client = PlaidClient(CREDENTIALS)
    if endpoint == "item_get":
        result = client.item_get("synthetic-access")
        assert "synthetic-private-body" not in repr(result)
    else:
        with pytest.raises(PlaidCallError) as caught:
            if endpoint == "link_token_create":
                client.link_token_create_hosted(
                    client_user_id="synthetic-user",
                    client_name="Synthetic",
                    products=("investments",),
                    country_codes=("US",),
                    language="en",
                )
            else:
                getattr(client, endpoint)("synthetic-token")
        assert "synthetic-private-body" not in str(caught.value)
    assert observed == [expected]


def test_silent_response_times_out_without_replay_and_next_call_runs(
    route: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wrapper, "_LINK_REQUEST_TIMEOUT", (0.1, 0.05))
    route["mode"] = "silent"
    client = PlaidClient(CREDENTIALS)
    with pytest.raises(PlaidCallError, match="ReadTimeoutError"):
        client.item_public_token_exchange("synthetic-first-public")
    assert len(route["paths"]) == 1
    route["mode"] = "success"
    assert client.item_public_token_exchange("synthetic-second-public").item_id == "synthetic-item"
    assert len(route["paths"]) == 2


def test_worker_keeps_sdk_timeout_uncertain_across_restart(
    route: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(wrapper, "_LINK_REQUEST_TIMEOUT", (0.1, 0.05))
    route["mode"] = "silent"
    sdk = PlaidClient(CREDENTIALS)

    class RealExchange(Client):
        def item_public_token_exchange(self, public_token: str) -> ExchangedItem:
            return sdk.item_public_token_exchange(public_token)

    client = RealExchange()
    path = tmp_path / "db.sqlite"
    tokens = TokenStore(tmp_path / "tokens")
    ref = tokens.put(SecretKind.LINK_TOKEN, FLOW, LINK)
    with sqlite3.connect(path) as db:
        migrate(db)
        db.execute(
            "INSERT INTO link_request(flow_id, secret_ref, minted_at, "
            "hosted_url_expires_at, state) VALUES (?, ?, ?, ?, 'URL_MINTED')",
            (FLOW, ref, STAMP, STAMP),
        )
        db.commit()
        outcome = run_request(
            db, tokens, client, flow_id=FLOW, country_codes=("US",), clock=lambda: NOW
        )
        assert outcome.failed
        assert db.execute("SELECT state, exchange_attempts FROM link_result").fetchone() == (
            "EXCHANGE_UNCERTAIN",
            1,
        )
    db.close()
    with sqlite3.connect(path) as restarted:
        run_request(
            restarted,
            TokenStore(tokens.directory),
            client,
            flow_id=FLOW,
            country_codes=("US",),
            clock=lambda: NOW,
        )
        assert restarted.execute("SELECT state, exchange_attempts FROM link_result").fetchone() == (
            "EXCHANGE_UNCERTAIN",
            1,
        )
    restarted.close()
    assert route["paths"] == ["/item/public_token/exchange"]
