"""The machine check behind `second_copy_holder`.

The field this feeds is the whole value of the second copy: a recovery record is
worth the machine it is on, so a holder name nobody measured is worse than no
field at all. Until the PR #75 re-review the absorber wrote the name as a
literal, and these tests exist so that cannot come back.

**The measurement itself is never stubbed.** :func:`holds_address` is exercised
with a real bind in both directions: `127.0.0.1`, which every machine holds, and
`192.0.2.1` — RFC 5737 TEST-NET-1, which no machine holds and which is reserved
precisely so it can be used this way.

:func:`verify` is a different matter, because the identity it demands is pinned
to a tailnet address only one computer in the world holds. It is therefore driven
by injecting ``probe``, which is the seam that exists for exactly this and is
reachable from nowhere on the command path. An earlier version took the required
identity from the environment so that these tests could stay "real" everywhere;
that made the production check redefinable — `NETWORTH_MAC_IDENTITY_ADDRESS=127.0.0.1`
passed on any machine — and the test convenience is not worth the bypass.
"""

from __future__ import annotations

import socket

import pytest

from networth import mac_identity

# Reserved for documentation by RFC 5737 and routable nowhere, so a bind to it
# fails on any machine this suite can run on.
UNHELD = "192.0.2.1"
HELD = "127.0.0.1"


def test_the_pinned_identity_is_this_project_s_mac() -> None:
    assert mac_identity.REQUIRED_HOLDER == "zelengs-macbook-air-2"
    assert mac_identity.REQUIRED_ADDRESS == "100.96.163.67"


def test_an_address_this_machine_holds_is_observed() -> None:
    assert mac_identity.holds_address(HELD) is True


def test_an_address_no_machine_holds_is_refused() -> None:
    assert mac_identity.holds_address(UNHELD) is False


def test_a_hostname_would_not_have_distinguished_anything() -> None:
    """Why the address is the signal, kept as a live check rather than a comment.

    The owner has several MacBook Airs whose names differ only by suffix, and the
    system hostname does not carry the suffix at all. If this ever starts failing
    because the hostname became specific, the check could get simpler — but it
    must not be *assumed* to have become specific.
    """
    assert socket.gethostname().lower() != mac_identity.REQUIRED_HOLDER


def test_verify_returns_the_pinned_holder_when_the_address_is_held() -> None:
    assert mac_identity.verify(probe=lambda _: True) == mac_identity.REQUIRED_HOLDER


def test_verify_asks_about_the_pinned_address_and_no_other() -> None:
    """The holder is not merely *a* measured identity; it is the design's one."""
    asked: list[str] = []

    def probe(address: str) -> bool:
        asked.append(address)
        return True

    mac_identity.verify(probe=probe)

    assert asked == [mac_identity.REQUIRED_ADDRESS]


def test_verify_refuses_when_the_address_is_not_held() -> None:
    with pytest.raises(mac_identity.WrongHost) as raised:
        mac_identity.verify(probe=lambda _: False)
    # The refusal has to say what it looked for: an operator on the wrong machine
    # needs to know which one it wanted, and the drivers quote this message.
    assert mac_identity.REQUIRED_ADDRESS in str(raised.value)
    assert mac_identity.REQUIRED_HOLDER in str(raised.value)


def test_the_environment_cannot_redefine_which_machine_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blocker this module was sent back for.

    The earlier version read the address and the holder from the environment, so

        NETWORTH_MAC_IDENTITY_ADDRESS=127.0.0.1 \
        NETWORTH_MAC_IDENTITY_HOLDER=definitely-not-the-pinned-mac

    made every machine pass — loopback is held everywhere — and stamped whatever
    name was supplied. These variables are set here deliberately: the point is not
    that the module ignores two particular names, it is that nothing in the
    environment reaches the decision at all, so the probe must still be asked
    about the pinned address and the returned holder must still be the pinned one.
    """
    monkeypatch.setenv("NETWORTH_MAC_IDENTITY_ADDRESS", HELD)
    monkeypatch.setenv("NETWORTH_MAC_IDENTITY_HOLDER", "definitely-not-the-pinned-mac")
    asked: list[str] = []

    def probe(address: str) -> bool:
        asked.append(address)
        return True

    assert mac_identity.verify(probe=probe) == mac_identity.REQUIRED_HOLDER
    assert asked == [mac_identity.REQUIRED_ADDRESS]


def test_the_real_probe_refuses_this_module_s_own_address_off_host() -> None:
    """`verify` with no injection is a genuine measurement, in whichever direction.

    On `zelengs-macbook-air-2` this returns the holder; anywhere else it raises.
    Asserting the machine-independent half — that the two agree — keeps the
    default wired to the real probe without pinning the suite to one computer.
    """
    held = mac_identity.holds_address(mac_identity.REQUIRED_ADDRESS)
    if held:
        assert mac_identity.verify() == mac_identity.REQUIRED_HOLDER
    else:
        with pytest.raises(mac_identity.WrongHost):
            mac_identity.verify()


def test_the_probe_binds_and_never_reaches_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proved by removing the network, not by asserting about it.

    A test that checked "connect was not called" would pass just as well against a
    probe that used `sendto`. This replaces the socket type with one whose every
    outbound operation raises, so any route to the network is a failure rather
    than an unasserted detail — and the bind still has to succeed underneath.
    """

    class NoNetwork(socket.socket):
        def connect(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("the identity probe must not connect")

        def connect_ex(self, *args: object, **kwargs: object) -> int:
            raise AssertionError("the identity probe must not connect")

        def send(self, *args: object, **kwargs: object) -> int:
            raise AssertionError("the identity probe must not send")

        def sendto(self, *args: object, **kwargs: object) -> int:
            raise AssertionError("the identity probe must not send")

    monkeypatch.setattr(socket, "socket", NoNetwork)
    assert mac_identity.holds_address(HELD) is True
    assert mac_identity.holds_address(UNHELD) is False
