"""The machine check behind `second_copy_holder`.

The field this feeds is the whole value of the second copy: a recovery record is
worth the machine it is on, so a holder name nobody measured is worse than no
field at all. Until the PR #75 re-review the absorber wrote the name as a
literal, and these tests exist so that cannot come back.

**Nothing here is stubbed at the point it is trying to prove.** The bind is real
in every test: the positive case uses `127.0.0.1`, which every machine holds, and
the negative uses `192.0.2.1` — RFC 5737 TEST-NET-1, which no machine holds and
which is reserved precisely so it can be used this way. That is why the module
takes its required identity from the environment: it lets the measurement stay
real on a CI runner that has never heard of a tailnet.
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
    assert mac_identity.DEFAULT_HOLDER == "zelengs-macbook-air-2"
    assert mac_identity.DEFAULT_ADDRESS == "100.96.163.67"


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
    assert socket.gethostname().lower() != mac_identity.DEFAULT_HOLDER


def test_verify_returns_the_holder_when_the_address_is_held() -> None:
    env = {mac_identity.ADDRESS_ENV: HELD, mac_identity.HOLDER_ENV: "test-host"}
    assert mac_identity.verify(env=env) == "test-host"


def test_verify_refuses_when_the_address_is_not_held() -> None:
    env = {mac_identity.ADDRESS_ENV: UNHELD, mac_identity.HOLDER_ENV: "test-host"}
    with pytest.raises(mac_identity.WrongHost) as raised:
        mac_identity.verify(env=env)
    # The refusal has to say what it looked for: an operator on the wrong machine
    # needs to know which one it wanted, and the drivers quote this message.
    assert UNHELD in str(raised.value)
    assert "test-host" in str(raised.value)


def test_an_empty_override_falls_back_to_the_pinned_identity() -> None:
    """An exported-but-empty variable must not disable the check.

    `FOO=` is a plausible accident in a shell profile, and `os.environ.get` would
    return `""` rather than the default — which would ask the kernel to bind the
    wildcard address, and the wildcard always binds.
    """
    env = {mac_identity.ADDRESS_ENV: "", mac_identity.HOLDER_ENV: ""}
    assert mac_identity.required_identity(env) == (
        mac_identity.DEFAULT_HOLDER,
        mac_identity.DEFAULT_ADDRESS,
    )


def test_the_default_identity_is_what_an_unset_environment_requires() -> None:
    assert mac_identity.required_identity({}) == (
        mac_identity.DEFAULT_HOLDER,
        mac_identity.DEFAULT_ADDRESS,
    )


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
