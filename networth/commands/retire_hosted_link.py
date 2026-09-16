"""``networth retire-hosted-link`` — the Mac half of a completion.

Reads the VPS completion transcript on **stdin**, passes all of it through, and
deletes this Mac's recovery record **only** when that transcript reports the flow
it was asked about as ``EXCHANGED``. It is the second half of
``scripts/link-complete.sh``; the first half is
:mod:`networth.commands.complete_hosted_link`, running on the VPS at the other
end of a pipe. The mirror image of :mod:`networth.commands.absorb_hosted_link`,
which is the second half of the mint.

**Why this exists as its own verb.** ``DESIGN.md`` §4 gives the interactive Mac
driver the job of deleting the second copy "as soon as the flow reports
``EXCHANGED``", leaving expiry to cover only the crash gap. Until the PR #75
re-review the delete lived inside the VPS verb, where it could not work: the
record is a file on ``zelengs-macbook-air-2`` and that process is on the sync
host. It found an empty directory and said nothing, so the *normal* path left
every completed flow's record on the Mac for seven hours and looked correct.
A cleanup cannot be performed by a machine that cannot see the thing.

**What authorises the deletion.** One marker line, carrying the flow id and the
outcome (:data:`~networth.link_recovery.COMPLETION_WIRE_MARKER`). Not the exit
status: zero means "the remote process did not fail", which is also true of
``--retrieve-only``, of a poll that timed out before any ``public_token``
existed, and of a refusal. Those are the states where the record is the only way
back to a live flow, and deleting it there destroys exactly the copy §4 exists to
keep. So this asks for a positive statement and treats its absence as "keep".

**And the flow id is compared, not trusted.** The marker names a flow; so does
``--flow``. If they disagree the transcript is describing a different run from
the one being retired — two overlapping rehearsals do that — and the deletion
would remove a live flow's copy while reporting success. That is a refusal.

**The permission it needs is the one it asks for.** This deletes a file in the
Mac's secrets directory, so it verifies it is the Mac (measured, not asserted)
before it touches anything, for the same reason the absorber does before it
writes.
"""

from __future__ import annotations

import argparse
import sys

from networth import link_recovery, mac_identity
from networth.link_recovery import CompletionOutcome, LinkRecoveryError

SUMMARY = "Retire this Mac's recovery record when a VPS transcript reports EXCHANGED (06a)."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--flow",
        metavar="ID",
        required=True,
        help=(
            "the flow this run is completing; the record retired is this one and "
            "the transcript must agree, so a transcript about another run cannot "
            "delete it"
        ),
    )
    parser.add_argument(
        "--expect-completion",
        action="store_true",
        help=(
            "the remote ran a mode that exchanges, so a transcript with no "
            "EXCHANGED marker is a failure rather than a quiet no-op. Omitted for "
            "--link-mode retrieve-only, which is non-destructive by design"
        ),
    )


def _read(lines: list[str]) -> CompletionOutcome | None:
    """Pass the transcript through; return the outcome if exactly one arrived.

    Two markers is a refusal rather than a choice, for the reason the absorber
    refuses two mint payloads: one run completes one flow, so a second line means
    the stream is not what this verb thinks it is, and choosing between them would
    be choosing which of the owner's recovery records to destroy.
    """
    found: list[CompletionOutcome] = []
    for line in lines:
        if line.startswith(link_recovery.COMPLETION_WIRE_MARKER):
            found.append(CompletionOutcome.from_wire(line))
        print(line)
    if not found:
        return None
    if len(found) > 1:
        raise LinkRecoveryError(
            f"the transcript carries {len(found)} completion markers; one run "
            "completes one flow, and choosing between them would be choosing "
            "which recovery record to delete"
        )
    return found[0]


def run(args: argparse.Namespace) -> int:
    lines = sys.stdin.read().splitlines()
    try:
        outcome = _read(lines)
    except LinkRecoveryError as exc:
        print(f"\nretire-hosted-link failed: {exc}", file=sys.stderr)
        return 2

    if outcome is None:
        if not args.expect_completion:
            # `--retrieve-only`'s normal ending. The flow is deliberately still
            # live and the record is what measurement (i) comes back to in 30
            # minutes, so "nothing was retired" is the success case here.
            print(f"\nrecord        kept for {args.flow}; nothing reported an exchange")
            return 0
        print(
            f"\nretire-hosted-link: the transcript never reported {args.flow} as "
            f"{link_recovery.EXCHANGED}, so this Mac's recovery record is kept. "
            "The remote half's own output is above; if the exchange did happen, the "
            "record is inert and the puller reaps it at reap_after",
            file=sys.stderr,
        )
        return 1

    if outcome.flow_id != args.flow:
        print(
            f"\nretire-hosted-link refused: the transcript completes "
            f"{outcome.flow_id}, not {args.flow}. Nothing was deleted — a record is "
            "the only way back to a flow that is still live, and this transcript is "
            "about a different one",
            file=sys.stderr,
        )
        return 2

    # Measured before anything is unlinked, like the absorber before anything is
    # written. Off-host this resolves some other machine's `~/agents/secrets`,
    # which `AGENTS.md` forbids this code from touching in either direction.
    try:
        mac_identity.verify()
    except mac_identity.WrongHost as exc:
        print(f"\nretire-hosted-link refused: {exc}", file=sys.stderr)
        print(
            "nothing was deleted. The exchange itself is unaffected — it already "
            "happened on the VPS, and its transcript is above",
            file=sys.stderr,
        )
        return 2

    directory = link_recovery.mac_recovery_directory()
    try:
        removed = link_recovery.delete(directory, outcome.flow_id)
    except (LinkRecoveryError, OSError) as exc:
        # A cleanup fault after a completed exchange is a note, never a failure:
        # the credential is stored and the flow is finished, so exiting non-zero
        # would send the owner to a recovery procedure for a flow that has none
        # left to do. The record is inert and `reap_after` bounds it.
        print(
            f"\nnote          this Mac's recovery record was not removed: {exc}",
            file=sys.stderr,
        )
        return 0

    if removed:
        print(f"\nretired       this Mac's recovery record for {outcome.flow_id}")
    else:
        # Not a fault either. `link-recover.sh` completes on this Mac and retires
        # in that process, and a re-run of a finished flow lands here.
        print(f"\nretired       nothing; {outcome.flow_id} had no record on this Mac")
    return 0
