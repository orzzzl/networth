"""Classify the live host's listeners without confusing private with absent.

Task 20's invariant has two deliberately separate halves: positively identify
``networth-serve`` on one of the node's current Tailscale addresses, then prove
that every *other* non-loopback, non-tailnet listener still equals the public
surface task 28 recorded.  The shared host legitimately has public SSH, so an
emptiness assertion would reject the correct machine and eventually be skipped.
"""

from __future__ import annotations

import ipaddress
import json
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

SERVE_PROCESS = "networth-serve"
SERVE_PORT = 8443


class ListenerCheckError(RuntimeError):
    """The live listener surface cannot be proved safe and reachable."""


@dataclass(frozen=True, slots=True, order=True)
class Listener:
    """A normalized ``ss -ltnpH`` row, excluding volatile pid/fd values."""

    address: str
    port: int
    processes: tuple[str, ...]

    @property
    def endpoint(self) -> str:
        host = f"[{self.address}]" if ":" in self.address else self.address
        return f"{host}:{self.port}"

    @property
    def description(self) -> str:
        owners = ",".join(self.processes) if self.processes else "<process unavailable>"
        return f"{self.endpoint} ({owners})"


@dataclass(frozen=True, slots=True)
class ListenerSurface:
    """The two independently verified halves of the bind invariant."""

    networth: Listener
    public_baseline: tuple[Listener, ...]


# The approved surface captured by task 28.  Keep one entry per reviewed
# opening so a deliberate host change is a small code review with a reason.
APPROVED_PUBLIC_BASELINE = (
    Listener("0.0.0.0", 22, ("sshd",)),  # Public SSH accepted in DESIGN §15.1.
    Listener("::", 22, ("sshd",)),  # The same SSH opening over IPv6.
)

_PROCESS_NAME = re.compile(r'\("([^"]+)"')


def _split_endpoint(raw: str) -> tuple[str, int]:
    if raw.startswith("["):
        close = raw.rfind("]:")
        if close < 0:
            raise ListenerCheckError(f"cannot parse listener endpoint {raw!r}")
        address = raw[1:close]
        port_text = raw[close + 2 :]
    else:
        address, separator, port_text = raw.rpartition(":")
        if not separator:
            raise ListenerCheckError(f"cannot parse listener endpoint {raw!r}")
    address = address.split("%", 1)[0]
    if not address:
        raise ListenerCheckError(f"listener endpoint {raw!r} has no address")
    try:
        port = int(port_text)
    except ValueError:
        raise ListenerCheckError(f"listener endpoint {raw!r} has no numeric port") from None
    if not 0 < port <= 65_535:
        raise ListenerCheckError(f"listener endpoint {raw!r} has an invalid port")
    return address, port


def parse_ss_listeners(output: str) -> tuple[Listener, ...]:
    """Parse ``ss -ltnpH`` output and retain the process identity if visible."""

    listeners: list[Listener] = []
    for number, line in enumerate(output.splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) < 5:
            raise ListenerCheckError(f"ss output line {number} has too few fields")
        address, port = _split_endpoint(fields[3])
        processes = tuple(sorted(set(_PROCESS_NAME.findall(line))))
        listeners.append(Listener(address, port, processes))
    return tuple(sorted(listeners))


def tailnet_addresses_from_status(output: str) -> frozenset[str]:
    """Read every address in ``TailscaleIPs``; the field is an array by design."""

    try:
        document = json.loads(output)
    except json.JSONDecodeError:
        raise ListenerCheckError("tailscale status did not return valid JSON") from None
    if not isinstance(document, dict):
        raise ListenerCheckError("tailscale status JSON is not an object")
    raw_addresses = document.get("TailscaleIPs")
    if not isinstance(raw_addresses, list) or not raw_addresses:
        raise ListenerCheckError("tailscale status has no non-empty TailscaleIPs array")

    addresses: set[str] = set()
    for raw in raw_addresses:
        if not isinstance(raw, str):
            raise ListenerCheckError("TailscaleIPs contains a non-text address")
        candidate = raw.split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(candidate)
        except ValueError:
            raise ListenerCheckError("TailscaleIPs contains an invalid IP address") from None
        if parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast:
            raise ListenerCheckError("TailscaleIPs contains an unusable bind address")
        addresses.add(str(parsed))
    return frozenset(addresses)


def select_bind_address(addresses: Iterable[str], requested: str | None = None) -> str:
    """Choose one live tailnet address, never a wildcard or loopback fallback."""

    normalized = frozenset(str(ipaddress.ip_address(value.split("%", 1)[0])) for value in addresses)
    if not normalized:
        raise ListenerCheckError("no tailnet address is available to bind")
    if requested is not None:
        try:
            selected = str(ipaddress.ip_address(requested.split("%", 1)[0]))
        except ValueError:
            raise ListenerCheckError("requested bind address is not an IP address") from None
        if selected not in normalized:
            raise ListenerCheckError(
                "requested bind address is not one of this node's current TailscaleIPs"
            )
        return selected

    ipv4 = sorted(address for address in normalized if ipaddress.ip_address(address).version == 4)
    return ipv4[0] if ipv4 else sorted(normalized)[0]


def _is_loopback(address: str) -> bool:
    if address == "*":
        return False
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def verify_listener_surface(
    listeners: Sequence[Listener],
    tailnet_addresses: Iterable[str],
    *,
    process: str = SERVE_PROCESS,
    port: int = SERVE_PORT,
    approved_public_baseline: Sequence[Listener] = APPROVED_PUBLIC_BASELINE,
) -> ListenerSurface:
    """Prove our exact bind and independently compare the host's public surface."""

    tailnet = frozenset(
        str(ipaddress.ip_address(address.split("%", 1)[0])) for address in tailnet_addresses
    )
    if not tailnet:
        raise ListenerCheckError("no tailnet address is available for listener verification")

    owned = tuple(listener for listener in listeners if process in listener.processes)
    if not owned:
        obscured = [
            listener for listener in listeners if listener.port == port and not listener.processes
        ]
        if obscured:
            raise ListenerCheckError(
                f"cannot identify the process owning port {port}; run ss with privilege for -p"
            )
        raise ListenerCheckError(f"no listener owned by process {process!r} was found")
    if len(owned) != 1:
        details = ", ".join(listener.description for listener in owned)
        raise ListenerCheckError(f"process {process!r} owns more than one listener: {details}")
    ours = owned[0]
    if ours.port != port:
        raise ListenerCheckError(
            f"{process} listens on unexpected port {ours.port}; expected {port}"
        )
    if ours.address not in tailnet:
        raise ListenerCheckError(
            f"{process} listener {ours.description} is not bound to a current TailscaleIP"
        )

    public = tuple(
        sorted(
            listener
            for listener in listeners
            if listener.address not in tailnet and not _is_loopback(listener.address)
        )
    )
    expected = tuple(sorted(approved_public_baseline))
    if public != expected:
        actual_text = ", ".join(listener.description for listener in public) or "<none>"
        expected_text = ", ".join(listener.description for listener in expected) or "<none>"
        raise ListenerCheckError(
            f"public listener baseline changed; expected [{expected_text}], found [{actual_text}]"
        )
    return ListenerSurface(networth=ours, public_baseline=public)


def _command_output(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError:
        raise ListenerCheckError(f"cannot execute {command[0]!r}") from None
    if completed.returncode != 0:
        raise ListenerCheckError(f"{command[0]!r} exited {completed.returncode}")
    return completed.stdout


def live_tailnet_addresses() -> frozenset[str]:
    return tailnet_addresses_from_status(_command_output(("tailscale", "status", "--json")))


def verify_live_listener_surface() -> ListenerSurface:
    addresses = live_tailnet_addresses()
    listeners = parse_ss_listeners(_command_output(("ss", "-ltnpH")))
    return verify_listener_surface(listeners, addresses)


__all__ = [
    "APPROVED_PUBLIC_BASELINE",
    "SERVE_PORT",
    "SERVE_PROCESS",
    "Listener",
    "ListenerCheckError",
    "ListenerSurface",
    "live_tailnet_addresses",
    "parse_ss_listeners",
    "select_bind_address",
    "tailnet_addresses_from_status",
    "verify_listener_surface",
    "verify_live_listener_surface",
]
