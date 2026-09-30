"""``networth classify-recovery`` — what state did the crashed recovery leave?

`07b` criterion 4 asks that each crash boundary *"leaves a state the next run can
classify correctly"*. :mod:`networth.link_crash` decides that; this verb is how
the owner reaches it, and it exists because the alternative is worse than
useless. Without it the classifier would be a module with tests and no caller —
correct, exercised, and unreachable from the thing that gets installed. This
project has that failure elsewhere and has written it down; shipping a second
instance in the module whose subject is *honest reporting* would be its own
punchline.

**It reads and it prints. It never writes, exchanges, restores or deletes**, so
it is safe to run in the middle of an emergency when the owner does not yet know
what happened — which is exactly when it will be run. That is also why it takes
the artifact path and the key as arguments rather than guessing: the run that
crashed was given both on its command line, and a verb that inferred them could
answer confidently about a different file.

**What it does not do is tell you to exchange again.** No state it can report
authorises that, and one of them —
:attr:`~networth.link_crash.CrashState.EXCHANGE_UNDECIDABLE` — is also what a
healthy first run looks like. The honest output there is that local evidence is
exhausted, which is a fact about this disk and not advice.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from networth import link_crash, link_recovery
from networth.backup import crypto

SUMMARY = "Say what state a crashed Mac-side link recovery left, from local evidence (07b)."

#: The shell exit status per state, and it is deliberately not "0 means fine".
#: The owner runs this after something went wrong, so the useful question a status
#: can answer is *"is there still a credential to rescue?"* — 0 when the answer is
#: settled and nothing is owed, 1 when something is owed and readable, 2 when the
#: evidence itself could not be established.
_EXIT = {
    link_crash.CrashState.CREDENTIAL_DURABLE: 0,
    link_crash.CrashState.NOTHING_HERE: 0,
    link_crash.CrashState.CREDENTIAL_LOST: 1,
    link_crash.CrashState.EXCHANGE_UNDECIDABLE: 1,
    link_crash.CrashState.CANNOT_TELL: 2,
}

#: What to do, per state. Kept beside the exit table rather than inside
#: :mod:`networth.link_crash` because it is operator instruction, not
#: classification — the classifier must stay usable by a caller that wants the
#: verdict without this verb's opinion about the owner's next hour.
_NEXT = {
    link_crash.CrashState.CREDENTIAL_DURABLE: (
        "Your credential survived. Do NOT exchange again and do NOT move or delete the "
        "artifact — it is the only copy of what the recovery recovered. Keep it and the "
        "escrowed backup key together until it is restored onto a replacement host. "
        "NOTE: that restore is `networth.link_sink.restore()`, a library function with "
        "no command of its own yet — 07b still owes the verb that runs it, so there is "
        "nothing for you to type here. This is not a step you are missing."
    ),
    link_crash.CrashState.CREDENTIAL_LOST: (
        "This artifact will not give up a credential. Treat the lifetime Item slot as "
        "spent: a second exchange of the same public_token was measured as ACCEPTED, so "
        "re-running is not a free retry. Quote the request_id printed by the run that "
        "crashed into a Plaid support ticket (issue #14) — that transcript is the only "
        "place it exists, because this host writes it inside the artifact and nowhere else."
    ),
    link_crash.CrashState.EXCHANGE_UNDECIDABLE: (
        "Local evidence is exhausted: this host records no exchange attempt, so a run "
        "that died after the response looks exactly like one that never started. If no "
        "run has been attempted, proceed normally. If one has, the transcript of it is "
        "the only evidence of whether a token was spent."
    ),
    link_crash.CrashState.NOTHING_HERE: (
        "Nothing to recover and nothing to finish. If you expected a record here, check "
        "the flow id and the recovery directory before concluding anything was lost."
    ),
    link_crash.CrashState.CANNOT_TELL: (
        "The evidence could not be read, which is not the same as there being none. Fix "
        "the access problem named above and ask again; do not exchange in the meantime."
    ),
}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--flow",
        metavar="ID",
        required=True,
        help="the flow whose recovery is being classified, as link-start.sh printed it",
    )
    parser.add_argument(
        "--artifact",
        metavar="PATH",
        required=True,
        help=(
            "the emergency artifact path the crashed run was given. Not guessed: a "
            "default would let this verb answer confidently about a different file"
        ),
    )
    parser.add_argument(
        "--backup-key",
        metavar="PATH",
        help=(
            "the escrowed 03a backup key. Without it an existing artifact can only be "
            "reported as unknown -- never as empty, since that report is the one a "
            "second exchange follows"
        ),
    )


def run(args: argparse.Namespace) -> int:
    key_bytes: bytes | None = None
    key_note = "not supplied"
    if args.backup_key:
        try:
            key_bytes = crypto.load_backup_key(Path(args.backup_key))
            key_note = f"loaded from {args.backup_key}"
        except crypto.BackupKeyError as exc:
            # Not fatal, and deliberately so: the record half of the classification
            # needs no key, and a run that cannot open the artifact still wants to
            # know whether the record is there. The state will be CANNOT_TELL, which
            # says the right thing.
            key_note = f"NOT usable ({exc})"

    directory = link_recovery.mac_recovery_directory()
    try:
        found = link_crash.classify(
            recovery_directory=directory,
            flow_id=args.flow,
            artifact_path=Path(args.artifact),
            key_bytes=key_bytes,
        )
    except link_recovery.LinkRecoveryError as exc:
        # `record_path` refuses a malformed flow id. Its own message never repeats
        # the rejected value, and neither does this.
        print(f"classify-recovery failed: {exc}", file=sys.stderr)
        return 2

    print(f"flow          {args.flow}")
    print(f"recovery dir  {directory}")
    print(f"backup key    {key_note}")
    print(f"record        {'present' if found.record_present else 'absent'}")
    print()
    print(f"state         {found.state.name}")
    print(f"              {found.state.value}")
    print(f"evidence      {found.detail}")
    print(f"credential    {'DURABLE' if found.credential_is_durable else 'not proven durable'}")
    print(
        "re-run risk   "
        + (
            "a re-run could exchange a second time"
            if found.rerun_may_exchange_again
            else "no record remains to exchange from"
        )
    )
    print()
    print(f"next          {_NEXT[found.state]}")
    return _EXIT[found.state]
