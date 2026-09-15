"""Which machine is this, measured rather than asserted.

`DESIGN.md` §4 puts the second copy of a pending Link's recovery record on
`zelengs-macbook-air-2`, and the value of that copy is entirely in *which machine
holds it*: it exists to survive losing the VPS. So a record stamped
`second_copy_holder=zelengs-macbook-air-2` is worth exactly as much as the
evidence behind the stamp, and until the PR #75 re-review there was none — the
absorber wrote the name unconditionally, so running the reviewed driver from any
other checkout produced a valid-looking record on the wrong computer, certifying
the one fact the design depends on.

**What is not a usable signal.** This Mac's system hostname is
`Zelengs-MacBook-Air.local`, and the owner has several MacBook Airs whose names
differ only by suffix. A hostname comparison would pass on the wrong machine,
which is worse than no check because it manufactures confidence.

**What is.** The tailnet address is pinned per machine in the design, and this
one is `100.96.163.67`. Holding an address is a property of the kernel's
interface table, not of a name anyone can set, so it distinguishes the four
Airs.

**How it is measured, and what it costs.** Binding a UDP socket to the address
succeeds only when some local interface holds it and fails with
``EADDRNOTAVAIL`` otherwise. Binding is not connecting: no packet leaves this
machine, nothing is resolved, and nothing reaches the network — which matters,
because the whole point of the surrounding code is that it makes exactly one
kind of outbound call and it is not this one.

**The override, and why it cannot forge a stamp.** Both the address and the name
come from the environment when set. That is what lets the test suite exercise a
*real* bind on any machine — every machine holds `127.0.0.1` and none holds
`192.0.2.1` — rather than stubbing out the measurement it is trying to prove. It
is not a hole in the guarantee: the invariant is *"the holder stamp names an
identity this machine provably held"*, and an override changes which identity is
required, never whether the bind actually succeeded. A record written under an
override names the identity that was checked, so it is still true.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable

DEFAULT_ADDRESS = "100.96.163.67"
DEFAULT_HOLDER = "zelengs-macbook-air-2"

ADDRESS_ENV = "NETWORTH_MAC_IDENTITY_ADDRESS"
HOLDER_ENV = "NETWORTH_MAC_IDENTITY_HOLDER"


class WrongHost(Exception):
    """This machine is not the one the record's holder field would claim."""


def holds_address(address: str) -> bool:
    """Does some local interface hold ``address``?

    Binds a datagram socket to it and reports whether the kernel allowed it.
    Nothing is sent and nothing is connected; an unbound UDP socket at port 0
    emits no traffic.
    """

    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as probe:
            probe.bind((address, 0))
    except OSError:
        return False
    return True


def required_identity(env: dict[str, str] | None = None) -> tuple[str, str]:
    """The ``(holder, address)`` this process requires itself to be."""

    source = os.environ if env is None else env
    return (
        source.get(HOLDER_ENV) or DEFAULT_HOLDER,
        source.get(ADDRESS_ENV) or DEFAULT_ADDRESS,
    )


def verify(
    *,
    env: dict[str, str] | None = None,
    probe: Callable[[str], bool] = holds_address,
) -> str:
    """Return the holder name this machine is entitled to, or raise.

    The returned name is the only value callers may stamp into a record: it is
    produced by the measurement rather than passed to it, so there is no path
    that writes a holder nobody checked.
    """

    holder, address = required_identity(env)
    if not probe(address):
        raise WrongHost(
            f"this machine does not hold {address}, so it is not {holder!r}. "
            "The second copy of a recovery record is only worth the machine it is "
            "on (DESIGN.md §4); writing one here would certify the wrong computer"
        )
    return holder
