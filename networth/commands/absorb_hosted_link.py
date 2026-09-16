"""``networth absorb-hosted-link`` — the Mac half of the mint, and the display boundary.

Reads the VPS mint transcript on **stdin**, keeps the one line that carries the
``link_token``, writes and byte-verifies this Mac's recovery record, and **only then**
prints the hosted URL. It is the second half of ``scripts/link-start.sh``; the first
half is :mod:`networth.commands.start_hosted_link`, running on the VPS at the other end
of a pipe.

**Why this exists as its own verb.** ``DESIGN.md`` §4 requires the second copy of a
pending Link's recovery record to be on ``zelengs-macbook-air-2`` and *read back*
before any URL is displayed, and §19 step 2a names the driver that does it. The VPS
verb cannot: a record it wrote would land on the machine whose loss the copy exists to
survive. So the ordering §4 asks for is only expressible across two processes, and this
is the one that holds the URL back.

**The refusal is the feature.** If the record cannot be written, or does not read back
identical, this prints **no URL** and exits non-zero. By **F2a** no Item slot is spent
until Link *completes*, so a run that stops here has cost nothing at all — while a URL
shown without a verified copy is openable, spendable, and recoverable only through a
file this machine just failed to store.

**What it does with everything that is not the payload.** The stream it reads is a
mixed transcript: ``sandbox-rehearsal-remote.sh`` prints the commit and the identity,
``sandbox-rehearsal.sh`` prints the workspace and the dependency verification, and the
mint verb prints the flow id. All of it is passed through to stdout unchanged, because
the owner is watching this run and a driver that swallowed the transcript to protect
one line of it would have made the run unreadable to protect a secret that is not in
the other lines. Exactly one line is withheld, identified by a marker rather than by
its shape.

**Nothing is written to a file but the record**, and the token never reaches ``argv``
— it arrives on a pipe, in this process's memory, and is handed to
:func:`~networth.link_recovery.store_and_verify`, which is the only writer.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from networth import link_recovery, mac_identity
from networth.link_recovery import LinkRecoveryError, MintResult

SUMMARY = "Absorb a VPS mint on stdin, verify this Mac's copy, then show the URL (06a)."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--commit",
        metavar="SHA",
        help=(
            "the reviewed commit the mint ran at; printed with the next command so "
            "the transcript says what the second half must be run against"
        ),
    )


def _absorb(lines: list[str]) -> MintResult | None:
    """Pass the transcript through; return the payload if exactly one arrived.

    Two payload lines is a refusal rather than a choice. One pipeline cannot mint
    twice, so a second line means the stream is not what this verb thinks it is,
    and picking either one would be picking which ``link_token`` the owner's
    recovery record names.
    """
    found: list[MintResult] = []
    for line in lines:
        if line.startswith(link_recovery.MINT_WIRE_MARKER):
            found.append(MintResult.from_wire(line))
            continue
        print(line)
    if not found:
        return None
    if len(found) > 1:
        raise LinkRecoveryError(
            f"the transcript carries {len(found)} mint payloads; one run mints once, "
            "and choosing between them would be choosing which token is recoverable"
        )
    return found[0]


def run(args: argparse.Namespace) -> int:
    lines = sys.stdin.read().splitlines()
    directory = link_recovery.mac_recovery_directory()
    try:
        result = _absorb(lines)
    except LinkRecoveryError as exc:
        # `exc` carries no material: `CorruptRecord` never echoes the payload, for
        # the reason its own docstring gives — the branch where a line is malformed
        # is the branch where its contents are arbitrary *and* next to a token.
        print(f"absorb-hosted-link failed: {exc}", file=sys.stderr)
        return 2

    if result is None:
        # Not a crash, and not a claim about why. The mint prints its own refusal
        # on the VPS and the transport prints its own; both are above, already
        # passed through. This says only that no URL is coming.
        print(
            "\nno mint payload in the transcript — nothing was minted, or the mint "
            "refused. No URL, and nothing has been spent (F2a)",
            file=sys.stderr,
        )
        return 1

    # Measured before the record is written, and the measurement *is* the value
    # stamped into it. Passing a literal here — which is what this did until the
    # PR #75 re-review — meant the field asserting which machine holds the second
    # copy was the one field nobody had checked, so a run from any other checkout
    # certified the wrong computer (`DESIGN.md` §4). There is deliberately no
    # parameter to override `holder` with: the only way to name a holder is to be
    # one.
    try:
        holder = mac_identity.verify()
    except mac_identity.WrongHost as exc:
        print(f"\nabsorb-hosted-link refused: {exc}", file=sys.stderr)
        print(
            "no record was written and no URL is printed. Nothing has been spent: "
            "the slot goes when Link completes (F2a) and nobody has been given a URL "
            "to complete. The link token on the VPS expires on Plaid's clock",
            file=sys.stderr,
        )
        return 2

    try:
        record = link_recovery.store_and_verify(
            directory,
            result.as_record(now=datetime.now(UTC)),
            holder=holder,
            now=datetime.now(UTC),
        )
    except LinkRecoveryError as exc:
        print(f"\nthe second copy did not verify: {exc}", file=sys.stderr)
        print(
            "no URL is printed and the run stops here. Nothing has been spent: the "
            "slot goes when Link completes (F2a), and nobody has been given a URL "
            "to complete. The link token on the VPS expires on Plaid's clock",
            file=sys.stderr,
        )
        return 2

    print()
    print(f"second copy   {link_recovery.record_path(directory, record.flow_id)}")
    print(f"verified      {record.second_copy_verified_at!r} by {record.second_copy_holder}")
    print(f"reap after    {record.reap_after!r} (this Mac's clock; local hygiene, not a deadline)")
    print()
    # The two clocks are never merged (issue #3), so neither is presented as "the"
    # deadline. Measurement (i) exists to put a number on the second, and until it
    # has run, 30 minutes is operative everywhere (06a acceptance (i)).
    print("open this URL, finish with user_good / pass_good, then come back:")
    print(f"  {result.hosted_link_url}")
    print()
    _print_next_command(record.flow_id, args.commit)
    return 0


def _print_next_command(flow_id: str, commit: str | None) -> None:
    """The follow-up, complete enough to paste.

    The previous version printed ``networth complete-hosted-link --flow <id>``,
    which argparse refuses: the mode is a required mutually-exclusive group, on
    purpose, because a default that exchanges is a default that spends. A command
    that exits at argument parsing is worse than no command on a path the owner is
    reading against a 30-minute clock, so all three forms are printed with what
    each one costs. Choosing is his; guessing would be spending his slot for him.

    The version after that printed ``sandbox-rehearsal-remote.sh`` directly, which
    runs the completion on the VPS and leaves the record this verb just wrote
    sitting on the Mac until the seven-hour sweep (`DESIGN.md` §4 wants the
    interactive driver to retire it on ``EXCHANGED``). So the command printed here
    is now ``link-complete.sh``, whose second half runs on this machine. The
    transport is still underneath it and still prints its own transcript.
    """
    where = commit if commit else "<the reviewed commit>"
    print("then, on this Mac, one of — the mode is required and never defaulted:")
    print(f"  ./scripts/link-complete.sh {where} --flow {flow_id} --link-mode retrieve-only")
    print("      poll and stop; nothing is exchanged, the record stays  (measurement (i))")
    print(f"  ./scripts/link-complete.sh {where} --flow {flow_id} --link-mode exchange")
    print("      exchange every public_token, then retire the record    (session 1's form)")
    print(f"  ./scripts/link-complete.sh {where} --flow {flow_id} --link-mode exchange-twice")
    print("      exchange, exchange again, probe the first, then retire (measurement (ii))")
