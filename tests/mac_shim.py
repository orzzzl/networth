"""Stub the Mac pre-flight for shell tests, without a switch in the product.

The drivers refuse to run anywhere but `zelengs-macbook-air-2`, measured by
binding the pinned tailnet address (`networth/mac_identity.py`). A CI runner is
not that machine, and neither is a second checkout, so the script-level tests
need the check to be answerable off-host.

The first attempt did that by letting `mac_identity` read the required address
and holder from the environment. That is the bypass PR #75 was sent back for:
`NETWORTH_MAC_IDENTITY_ADDRESS=127.0.0.1` passes on *every* machine, and it sat
on the owner-facing command path. A testability mechanism must not be reachable
from the command the owner runs.

So the substitution happens here instead, in the test tree, through
``sitecustomize`` on the child's ``PYTHONPATH`` — the same technique
`test_command_complete_hosted_link.py` uses to remove the network. The product
has no knowledge of it, nothing on the command path consults it, and the decision
it replaces is unit-tested directly and for real in `test_mac_identity.py`, where
both directions of the bind are exercised against `127.0.0.1` and `192.0.2.1`.
"""

from __future__ import annotations

from pathlib import Path

#: Read only by the shim below. Deliberately not a `NETWORTH_*` name that could be
#: mistaken for configuration the product honours.
HOLDS_ENV = "NETWORTH_TEST_MAC_HOLDS"


def mac_shim(tmp_path: Path, *, holds: bool = True) -> tuple[Path, dict[str, str]]:
    """A ``sitecustomize`` directory and the environment that arms it.

    ``holds=False`` makes the child behave as though it is the wrong computer,
    which is how the refusal paths are exercised without depending on the address
    the runner happens to hold.
    """

    shim = tmp_path / "mac-shim"
    shim.mkdir(exist_ok=True)
    (shim / "sitecustomize.py").write_text(
        "import os\n"
        "\n"
        "import networth.mac_identity as _identity\n"
        "\n"
        "# `verify` resolves this attribute at call time rather than binding it as\n"
        "# a default, so replacing it here reaches every caller in the child.\n"
        f"_held = os.environ.get({HOLDS_ENV!r}) == '1'\n"
        "_identity.holds_address = lambda _address: _held\n",
        encoding="utf-8",
    )
    return shim, {HOLDS_ENV: "1" if holds else "0"}
