"""The fence: the old worker is off, and the owner is the only one who can say so.

`tasks/README.md` `07b`, criterion 6:

    **A fencing precondition that is real, because the shared claim is not.** … The
    fence is: **before the exchange, the script requires evidence that the old VPS
    worker is not running** — the owner powers off or destroys the instance in the
    provider control plane and the script takes an explicit typed confirmation naming
    that instance, recorded with the recovery artifact. **Unreachable is not off**: a
    host that fails a ping can still be mid-``/link/token/get``. State plainly in the
    owner-facing text that this host is also his exit node (§15.1), so powering it off
    is a real decision and not a formality.

**Why this module contains no probe, and could not.** Everything code on this Mac
could measure about the sync host is *reachability*, and the criterion rules that out
in the same breath as it asks for the fence: a host that fails a ping can still be in
the middle of ``/link/token/get``, and it is that call — not its liveness on the
network — which would spend the same one-time ``public_token`` this recovery is about.
A probe would also be **unable to tell the emergency from the danger**: "the VPS does
not answer" is the premise of the whole procedure. Its pass and its fail would carry
the same information, which is none.

**So the fence is an attestation, and this module says so rather than dressing it up
as a measurement.** What it buys is not a proof — it is that the power-off becomes a
step the owner performed and a fact the recovery record carries, instead of an
assumption the code made on his behalf. The two properties it must have are therefore
(1) that it cannot be satisfied without a deliberate act, which is why the answer is a
**typed name** and not a ``y/N``, and (2) that what he attested is **recorded with the
credential**, which is :class:`FenceAttestation` travelling inside the sealed artifact.

**What the local conditional claim does not do**, restated here because this module is
what stands in for it. `07b`'s must-not: *"Do not present the local conditional claim
as protection against the old VPS. A lock in a database the other host cannot open is
a comment, and one that reads like a guarantee is worse than none — it is the failure
mode where the owner skips the power-off because the script implied it was covered."*
The claim serialises two runs **on this Mac**. It says nothing about the other host,
and this fence is the only thing in the system that speaks to it at all.

**Why the name is pinned here rather than read from the environment.** The same reason
:mod:`networth.mac_identity` has no override: a value the owner-facing command can
redefine at runtime is not a check, it is a prompt with a default answer, and
``NETWORTH_FENCED_INSTANCE=x`` would satisfy this by typing ``x``. The name is also
already in this repo (``networth/commands/backup.py`` defaults ``--ssh-host`` to it),
so pinning it here reveals nothing that was not public.

**The name is the one this project knows the host by**, which is not necessarily the
label the provider's control plane shows. That is deliberate and is the honest scope of
the attestation: what has to be off is the host that runs *this project's* Link worker,
and that is the host this repo names. Matching a provider label the repo has never seen
would be a check on a string nothing here can verify.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

#: Pinned, not defaulted and not overridable — see the module docstring. This is the
#: name this project knows its sync host by; `DESIGN.md` §15.1 is what makes powering
#: it off a decision rather than a formality, because it is the owner's Tailscale exit
#: node and was that before this project existed.
FENCED_INSTANCE: Final = "tokyo-exit"

#: What the owner is attesting to, in one sentence, stored beside his confirmation.
#: The statement travels with the record on purpose: a recovery artifact read months
#: later has to say *what* was confirmed, not merely that something was. A bare
#: ``fenced: true`` would be a claim whose meaning lives only in the code that wrote it.
ATTESTATION: Final = (
    "powered off or destroyed in the provider control plane, not merely unreachable"
)


class FenceError(Exception):
    """Base. No message here carries anything the owner typed — see :func:`confirm`."""


class FenceNotConfirmed(FenceError):
    """The fence was not confirmed, so nothing may be exchanged.

    Raised **before** the one-time ``public_token`` is spent, which is the only
    property that matters about it: refusing here costs the recovery nothing but the
    rest of the 30-minute window.
    """


class FenceRecordInvalid(FenceError):
    """A stored attestation is missing, malformed, or not a timestamped statement."""


@dataclass(frozen=True, slots=True)
class FenceAttestation:
    """One owner confirmation, in the shape that survives into the artifact.

    Three fields, and each is load-bearing for a *reader*: which host was fenced, what
    was claimed about it, and when. ``confirmed_at`` must be timezone-aware — the same
    rule the reminder queue applies to ``due:``, for the same reason: an instant whose
    offset is implied cannot be compared to the exchange it is supposed to precede.
    """

    instance: str
    statement: str
    confirmed_at: datetime

    def __post_init__(self) -> None:
        # Validated in the constructor rather than at each boundary, so a record read
        # back from an artifact and one built at the prompt cannot differ in what they
        # guarantee. `from_record` is the untrusted direction and gets the same check
        # for free.
        if not self.instance or not self.statement:
            raise FenceRecordInvalid("a fence attestation names an instance and a statement")
        if self.confirmed_at.tzinfo is None or self.confirmed_at.utcoffset() is None:
            raise FenceRecordInvalid(
                "a fence attestation's confirmed_at must carry a UTC offset; a naive "
                "instant cannot be placed before or after the exchange it fences"
            )

    def to_record(self) -> dict[str, str]:
        """The JSON shape sealed inside the artifact."""
        return {
            "instance": self.instance,
            "statement": self.statement,
            "confirmed_at": self.confirmed_at.isoformat(),
        }

    @classmethod
    def from_record(cls, record: object) -> FenceAttestation:
        """Read one back, refusing anything that is not a timestamped statement.

        The refusals are deliberately specific about *which* part is wrong: this runs
        during a restore, which is the second half of an emergency, and "the artifact
        is bad" is not an answer anyone can act on.
        """
        if not isinstance(record, dict):
            raise FenceRecordInvalid(
                "no fence confirmation is recorded; 07b criterion 6 requires one with "
                "the credential, so this is not a complete recovery artifact"
            )
        fields: dict[str, str] = {}
        for name in ("instance", "statement", "confirmed_at"):
            value = record.get(name)
            if not isinstance(value, str) or not value:
                raise FenceRecordInvalid(f"the recorded fence confirmation has no usable {name!r}")
            fields[name] = value
        try:
            confirmed_at = datetime.fromisoformat(fields["confirmed_at"])
        except ValueError:
            raise FenceRecordInvalid(
                "the recorded fence confirmation's confirmed_at is not an ISO 8601 instant"
            ) from None
        return cls(
            instance=fields["instance"],
            statement=fields["statement"],
            confirmed_at=confirmed_at,
        )


def prompt_text() -> str:
    """The owner-facing text, at the one moment he is deciding.

    This is where criterion 6's *"state plainly in the owner-facing text that this host
    is also his exit node"* lands, rather than only in a script header he read once:
    the sentence is worth having where the cost is being incurred. It says what has to
    be true, why unreachable does not count, what it costs him, and that what he types
    is kept.

    **It deliberately does not print the name he has to type.** A prompt that both asks
    the question and supplies the answer is friction, not evidence — and the whole
    value here is that the person confirming has been to the control plane. The refusal
    in :func:`confirm` names where the expected value is written, so a mistyped or
    renamed host is recoverable rather than a dead end on a 30-minute clock.
    """
    return (
        "\n"
        "FENCE — required before the exchange (07b criterion 6)\n"
        "\n"
        "  The host that runs this project's Link worker must be POWERED OFF or\n"
        "  DESTROYED in your provider's control plane. Unreachable is not off: a host\n"
        "  that fails a ping can still be in the middle of /link/token/get, and that\n"
        "  call spends the same one-time public_token this recovery is about.\n"
        "\n"
        "  That host is also your Tailscale exit node (DESIGN.md 15.1). Powering it\n"
        "  off takes your VPN exit with it. This is a real decision, which is why\n"
        "  nothing here makes it for you and no ping is treated as an answer.\n"
        "\n"
        "  Your confirmation is sealed inside the recovery artifact with the\n"
        "  credential, so the record says what was fenced and when.\n"
        "\n"
        "Name the host you have powered off or destroyed: "
    )


def confirm(ask: Callable[[str], str], *, now: datetime) -> FenceAttestation:
    """Ask, and return the attestation — or refuse, with nothing exchanged.

    ``ask`` is injected rather than imported so the caller owns *how* the terminal is
    read (:func:`networth.commands.complete_hosted_link._read_from_tty` opens
    ``/dev/tty`` by name and refuses a pipe), and so this logic is testable without a
    PTY. It is the same seam :func:`networth.mac_identity.verify` uses for its probe,
    for the same reason: the decision is worth unit-testing, and the syscalls are worth
    exercising exactly once, on the real thing.

    **No refusal quotes what was typed.** This prompt sits one step above the one that
    reads a Plaid secret, and a mistyped answer echoed into a refusal would put
    whatever was in the paste buffer into a transcript the owner may well be piping to
    a file. The refusal says where the expected name is written instead, which is the
    part he can act on.
    """
    typed = ask(prompt_text()).strip()
    if not typed:
        raise FenceNotConfirmed(
            "the fence was not confirmed: nothing was typed. Nothing has been "
            "exchanged, no Item slot is at risk, and the public_token is still good "
            "for the rest of its 30 minutes"
        )
    # Case is not the attestation, the name is; a host typed in the wrong case mid
    # emergency is a typo, not a different host. The canonical spelling is what gets
    # recorded, so no later reader has to compare two spellings of one host.
    if typed.casefold() != FENCED_INSTANCE.casefold():
        raise FenceNotConfirmed(
            "what was typed does not name the host this recovery is fenced against. "
            "That name is pinned as FENCED_INSTANCE in networth/link_fence.py and is "
            "the name this project knows its sync host by; type it exactly. Nothing "
            "has been exchanged"
        )
    return FenceAttestation(
        instance=FENCED_INSTANCE,
        statement=ATTESTATION,
        confirmed_at=now,
    )
