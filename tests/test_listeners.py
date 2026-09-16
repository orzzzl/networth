"""Task 20's two-part listener invariant and its awkward live-host cases."""

from __future__ import annotations

import json

import pytest

from networth.listeners import (
    Listener,
    ListenerCheckError,
    parse_ss_listeners,
    select_bind_address,
    tailnet_addresses_from_status,
    verify_listener_surface,
)

TAILNET_V4 = "100.102.245.37"
TAILNET_V6 = "fd7a:115c:a1e0::1d37:f526"
TAILNET = frozenset({TAILNET_V4, TAILNET_V6})


def _line(endpoint: str, peer: str, process: str | None) -> str:
    owner = "" if process is None else f' users:(("{process}",pid=42,fd=3))'
    return f"LISTEN 0 4096 {endpoint} {peer}{owner}"


def _correct_output(*, networth_address: str = TAILNET_V4) -> str:
    endpoint = (
        f"[{networth_address}]:8443" if ":" in networth_address else f"{networth_address}:8443"
    )
    return "\n".join(
        [
            _line("0.0.0.0:22", "0.0.0.0:*", "sshd"),
            _line("[::]:22", "[::]:*", "sshd"),
            _line(endpoint, "0.0.0.0:*", "networth-serve"),
            _line("127.0.0.54:53", "0.0.0.0:*", "systemd-resolve"),
            _line("127.0.0.53%lo:53", "0.0.0.0:*", "systemd-resolve"),
            _line(f"[{TAILNET_V6}]:47618", "[::]:*", "tailscaled"),
        ]
    )


def test_tailscale_status_reads_the_whole_address_array() -> None:
    status = json.dumps({"TailscaleIPs": [TAILNET_V4, TAILNET_V6]})

    assert tailnet_addresses_from_status(status) == TAILNET
    assert select_bind_address(TAILNET) == TAILNET_V4
    assert select_bind_address(TAILNET, TAILNET_V6) == TAILNET_V6


def test_listener_check_separates_our_socket_from_the_public_baseline() -> None:
    listeners = parse_ss_listeners(_correct_output())

    surface = verify_listener_surface(listeners, TAILNET)

    assert surface.networth == Listener(TAILNET_V4, 8443, ("networth-serve",))
    assert surface.public_baseline == (
        Listener("0.0.0.0", 22, ("sshd",)),
        Listener("::", 22, ("sshd",)),
    )


def test_tailnet_ipv6_is_as_valid_as_the_first_array_element() -> None:
    listeners = parse_ss_listeners(_correct_output(networth_address=TAILNET_V6))

    surface = verify_listener_surface(listeners, TAILNET)

    assert surface.networth.address == TAILNET_V6


@pytest.mark.parametrize(
    "wrong_address",
    ["0.0.0.0", "203.0.113.10", "::", "127.0.0.1"],
)
def test_wildcard_public_and_loopback_networth_binds_all_fail(wrong_address: str) -> None:
    listeners = parse_ss_listeners(_correct_output(networth_address=wrong_address))

    with pytest.raises(ListenerCheckError, match="not bound to a current TailscaleIP"):
        verify_listener_surface(listeners, TAILNET)


def test_process_identity_is_required_instead_of_accepting_the_expected_port() -> None:
    output = _correct_output().replace(
        _line(f"{TAILNET_V4}:8443", "0.0.0.0:*", "networth-serve"),
        _line(f"{TAILNET_V4}:8443", "0.0.0.0:*", None),
    )

    with pytest.raises(ListenerCheckError, match="run ss with privilege"):
        verify_listener_surface(parse_ss_listeners(output), TAILNET)


def test_interface_scoped_loopback_is_not_misclassified_as_public() -> None:
    listeners = parse_ss_listeners(_correct_output())

    scoped = next(listener for listener in listeners if listener.port == 53)
    surface = verify_listener_surface(listeners, TAILNET)

    assert scoped.address in {"127.0.0.53", "127.0.0.54"}
    assert all(listener.port != 53 for listener in surface.public_baseline)


def test_any_new_public_listener_fails_and_names_its_process() -> None:
    output = _correct_output() + "\n" + _line("203.0.113.10:9000", "0.0.0.0:*", "other-daemon")

    with pytest.raises(ListenerCheckError, match="other-daemon"):
        verify_listener_surface(parse_ss_listeners(output), TAILNET)


def test_requested_bind_must_be_one_of_the_current_tailnet_addresses() -> None:
    with pytest.raises(ListenerCheckError, match="not one of"):
        select_bind_address(TAILNET, "127.0.0.1")


def test_missing_tailnet_address_refuses_instead_of_falling_back_to_wildcard() -> None:
    with pytest.raises(ListenerCheckError, match="no tailnet address"):
        select_bind_address(())
