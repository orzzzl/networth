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

**And it reports two facts, never one.** Whether a marker proved this flow's
exchange landed, and what is on this Mac's disk afterwards, are independent —
the second re-review found them collapsed into a single word, which made a
post-exchange cleanup fault indistinguishable from a flow that may still be
live, and those two want opposite things from the owner. Neither is ever
inferred from the other, and the second one is looked at rather than deduced —
through a lookup that keeps its errors, because a question that could not be
answered is not an absence. See :func:`_record_outcome` and
:func:`_observe_record`.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path
from typing import Final

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
    parser.add_argument(
        "--outcome-file",
        metavar="PATH",
        help=(
            "write 'exchange=<exchanged|unproven>' and 'record=<present|absent|"
            "unknown>' here, for the driver that has to describe afterwards what "
            "happened. Two facts, never one: the exit status carries neither (this "
            "verb exits 0 both when it deletes and when it deliberately keeps), and "
            "collapsing them made a post-exchange cleanup fault indistinguishable "
            "from a flow that may still be live"
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


#: Did a marker prove *this* flow's exchange landed? The first of the two facts
#: the driver needs, and the one the exit status cannot carry.
EXCHANGED: Final = "exchanged"
UNPROVEN: Final = "unproven"

#: And, separately, what is on this Mac's disk afterwards. `UNKNOWN` is not a
#: hedge: it is the answer on every path that returns before this process has
#: established it is even the machine that holds the record, where looking would
#: mean resolving some other computer's `~/agents/secrets`.
PRESENT: Final = "present"
ABSENT: Final = "absent"
UNKNOWN: Final = "unknown"


def _record_outcome(path: str | None, *, exchange: str, record: str) -> None:
    """Tell the driver both facts, if it asked.

    **Both, keyword-only, with no defaults, because the re-review's blocker was
    exactly that they had been one word.** A single `kept` meant "no marker
    arrived, so the flow may still be live and the record is the way back" *and*
    "the marker proved the exchange landed, but cleanup faulted, so the record is
    inert residue" — opposite instructions to the owner under one name. The old
    vocabulary also could not express the state that produced the reproduction:
    `delete()` unlinks first and fsyncs the directory second, so a fault in the
    fsync leaves the pathname already gone while the report said `kept`.

    A signature that cannot express one fact without the other is the part of
    this fix that survives someone editing the call sites.

    Never raises. This runs at the end of paths that have already succeeded or
    already failed, and a report that could itself fail would turn a diagnostic
    into a second fault. A driver that finds no file says it cannot tell, which
    is the honest reading of "the half that knew did not get to speak".
    """
    if path is None:
        return
    with contextlib.suppress(OSError):
        Path(path).write_text(f"exchange={exchange}\nrecord={record}\n", encoding="utf-8")


def _observe_record(directory: Path, flow_id: str) -> str:
    """Look at the record; report what is there.

    Called only after :func:`mac_identity.verify` has passed, so it is this Mac's
    own directory being read. Every earlier path reports :data:`UNKNOWN` instead
    of calling this, which is the same rule the previous round established for
    the delete itself: the machine that cannot see the file does not get to
    describe it.

    Used on the success path too, rather than writing :data:`ABSENT` from the
    fact that `delete()` returned. That is a small thing that keeps a large
    promise: nothing in this report is ever inferred from an operation's outcome,
    including an operation that went well.

    **A lookup that failed is not an absence, and `Path.exists()` cannot tell the
    difference.** It answers a boolean, so it has to spend the error to do it. The
    third re-review found the consequence and measuring it found something worse
    than one wrong answer — the same source reported *opposite* facts on two
    interpreters this project already accepts (``requires-python = ">=3.12"``),
    for a record that was present and unreadable:

    ==================  ============================  =======================
    interpreter         ``Path.exists()``             this function said
    ==================  ============================  =======================
    3.12.3 — CI's       raises ``PermissionError``    ``unknown`` — right
    3.14.7 — review's   returns ``False``             ``absent`` — a lie
    ==================  ============================  =======================

    So the `except OSError` that promised :data:`UNKNOWN` was live on some
    machines and dead on others, for the same source on the same disk. **CI is on
    the sound side of that table** — ``ubuntu-latest``'s ``uv sync`` resolves
    ``/usr/bin/python3``, 3.12.3 — which is worse than it sounds: the defect was
    invisible to the gate, and a regression written only around the reported
    ``EACCES`` would be green there forever. `lstat` has no boolean to protect,
    so every lookup failure arrives as itself on every interpreter.

    :data:`ABSENT` is then reserved for the two errnos that *state* there is no
    file at the pathname — ``ENOENT``, and ``ENOTDIR`` for a non-directory
    component, which is the same statement reached one component earlier. Every
    other error means the question was not answered: ``EACCES`` above, and
    ``ELOOP``, which is the one the regression is built on because `exists()`
    reports it as ``False`` on *both* rows. A symlink loop is the opposite of an
    absence.

    `lstat` rather than `stat` because the fact being reported is about the
    directory entry `delete()` unlinks, and `unlink` does not follow the final
    component either. A dangling symlink there is residue that is still on this
    disk, and `stat` would call it :data:`ABSENT`.
    """
    try:
        link_recovery.record_path(directory, flow_id).lstat()
    except (FileNotFoundError, NotADirectoryError):
        return ABSENT
    except OSError:
        return UNKNOWN
    return PRESENT


def run(args: argparse.Namespace) -> int:
    lines = sys.stdin.read().splitlines()
    try:
        outcome = _read(lines)
    except LinkRecoveryError as exc:
        print(f"\nretire-hosted-link failed: {exc}", file=sys.stderr)
        _record_outcome(args.outcome_file, exchange=UNPROVEN, record=UNKNOWN)
        return 2

    if outcome is None:
        if not args.expect_completion:
            # `--retrieve-only`'s normal ending. The flow is deliberately still
            # live and the record is what measurement (i) comes back to in 30
            # minutes, so "nothing was retired" is the success case here.
            print(f"\nrecord        kept for {args.flow}; nothing reported an exchange")
            _record_outcome(args.outcome_file, exchange=UNPROVEN, record=UNKNOWN)
            return 0
        print(
            f"\nretire-hosted-link: the transcript never reported {args.flow} as "
            f"{link_recovery.EXCHANGED}, so this Mac's recovery record is kept. "
            "The remote half's own output is above; if the exchange did happen, the "
            "record is inert and the puller reaps it at reap_after",
            file=sys.stderr,
        )
        _record_outcome(args.outcome_file, exchange=UNPROVEN, record=UNKNOWN)
        return 1

    if outcome.flow_id != args.flow:
        print(
            f"\nretire-hosted-link refused: the transcript completes "
            f"{outcome.flow_id}, not {args.flow}. Nothing was deleted — a record is "
            "the only way back to a flow that is still live, and this transcript is "
            "about a different one",
            file=sys.stderr,
        )
        _record_outcome(args.outcome_file, exchange=UNPROVEN, record=UNKNOWN)
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
        _record_outcome(args.outcome_file, exchange=EXCHANGED, record=UNKNOWN)
        return 2

    directory = link_recovery.mac_recovery_directory()
    try:
        path = link_recovery.record_path(directory, outcome.flow_id)
        if path.exists() and link_recovery.load(directory, outcome.flow_id).hosted_url is not None:
            print("Automatic recovery record retained; one child cannot retire its request.")
            _record_outcome(
                args.outcome_file,
                exchange=EXCHANGED,
                record=_observe_record(directory, outcome.flow_id),
            )
            return 0
        removed = link_recovery.delete(directory, outcome.flow_id)
    except (LinkRecoveryError, OSError) as exc:
        # A cleanup fault after a completed exchange is a note, never a failure:
        # the credential is stored and the flow is finished, so exiting non-zero
        # would send the owner to a recovery procedure for a flow that has none
        # left to do. The record is inert and `reap_after` bounds it.
        #
        # And what "the record" means here is *looked at*, not assumed from the
        # exception. `delete()` unlinks first and fsyncs the directory second, so
        # a fault raised by that fsync leaves the pathname already gone — the
        # reproduction in the re-review, where the report said `kept` about a
        # file that no longer existed. The state after a partial operation is not
        # derivable from the fact that it raised.
        state = _observe_record(directory, outcome.flow_id)
        print(
            f"\nnote          this Mac's recovery record was not removed cleanly: "
            f"{exc}\nrecord        {state} afterwards; the exchange itself landed, so "
            "it is inert either way",
            file=sys.stderr,
        )
        _record_outcome(args.outcome_file, exchange=EXCHANGED, record=state)
        return 0

    if removed:
        print(f"\nretired       this Mac's recovery record for {outcome.flow_id}")
    else:
        # Not a fault either. `link-recover.sh` completes on this Mac and retires
        # in that process, and a re-run of a finished flow lands here.
        print(f"\nretired       nothing; {outcome.flow_id} had no record on this Mac")
    _record_outcome(
        args.outcome_file,
        exchange=EXCHANGED,
        record=_observe_record(directory, outcome.flow_id),
    )
    return 0
