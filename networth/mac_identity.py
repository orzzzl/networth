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

**There is no override, and the first version of this module was wrong to have
one.** It read both the address and the name from the environment so the suite
could bind for real on any machine, and defended that with the invariant *"the
holder stamp names an identity this machine provably held"*. That is a weaker
claim than the design makes. §4 does not need the record to name some machine
that was checked; it needs the record to be **on `zelengs-macbook-air-2`**,
because the copy's entire value is surviving the loss of the VPS. With the
environment in the loop, `NETWORTH_MAC_IDENTITY_ADDRESS=127.0.0.1` passes
everywhere — every machine holds loopback — and the holder string is whatever was
supplied. Moving an override from a function argument to the environment does not
remove it; it only moves it somewhere the owner-facing command still reads.

So the required identity is pinned here and cannot be redefined at runtime. The
seam for tests is :func:`verify`'s ``probe`` argument, which is dependency
injection at the one place the measurement happens, reachable from unit tests and
from nowhere on the command path. Shell integration tests stub the pre-flight
subcommand itself rather than re-defining what it checks.
"""

from __future__ import annotations

import socket
from collections.abc import Callable

#: Pinned, not defaulted. `AGENTS.md` requires the owner's machines to be named
#: in full, and there are four MacBook Airs on this tailnet.
REQUIRED_ADDRESS = "100.96.163.67"
REQUIRED_HOLDER = "zelengs-macbook-air-2"


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


def verify(*, probe: Callable[[str], bool] | None = None) -> str:
    """Return the holder name this machine is entitled to, or raise.

    The returned name is the only value callers may stamp into a record: it is
    produced by the measurement rather than passed to it, so there is no path
    that writes a holder nobody checked — and it is
    :data:`REQUIRED_HOLDER` or nothing, so there is no path that writes a holder
    the design did not ask for either.

    ``probe`` is the only seam, and it is deliberately not reachable from the
    command line: a test may replace the measurement, but no environment can
    change *which* identity is demanded.

    Resolved here rather than as a default argument value so that the module
    attribute stays the live one — a default binds :func:`holds_address` at
    definition time, which would quietly ignore anything substituted for it
    later and make a stubbed test pass against the real bind.
    """

    measure = holds_address if probe is None else probe
    if not measure(REQUIRED_ADDRESS):
        raise WrongHost(
            f"this machine does not hold {REQUIRED_ADDRESS}, so it is not "
            f"{REQUIRED_HOLDER!r}. "
            "The second copy of a recovery record is only worth the machine it is "
            "on (DESIGN.md §4); writing one here would certify the wrong computer"
        )
    return REQUIRED_HOLDER
