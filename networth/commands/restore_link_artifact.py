"""``networth restore-link-artifact`` — the sealed recovery, put back into service.

`07b` criterion 1 requires the emergency artifact to have *"a restore path into a
real ``TokenStore``"*, and :func:`networth.link_sink.restore` is that path. Until
this verb existed it was **a library function with no caller**: correct, covered by
tests, and unreachable from anything the owner can run. `classify-recovery`'s own
guidance had to say so out loud — *"07b still owes the verb that runs it"* — which
is the honest version of a gap and not a substitute for closing it. This project has
shipped an unreachable component before and written the lesson down; leaving the
**recovery** of a lifetime Item as the second instance would be the worst place for
it.

**This is the only verb here that may run against Production, and that is the
point.** `complete-hosted-link` refuses anything but Sandbox because it is a
measurement that exchanges real tokens. This one exchanges nothing: it opens a file
the owner already has and moves a credential that already exists into the place the
daemon reads. Refusing Production here would refuse the entire scenario `07b` is
about — a Production Item recovered after the VPS was lost — so the environment
selects the destination and never gates the run.

**Where it runs.** On the replacement host, not on ``zelengs-macbook-air-2``: the
destination is ``NETWORTH_ENV``'s own ``TokenStore`` and database, and §15 keeps
runtime secrets off the laptop. The artifact and the escrowed key are what travel.

**Two halves, reported separately, because they fail separately.** The credential
half makes the Item usable. The pairing half — criterion 3 — writes the recovered
``item_id`` onto the Link success row so `26a` counts one lifetime slot instead of
two. Exit 0 means both; :data:`~networth.link_sink.INCOMPLETE_RECOVERY` means the
credential is durable and the accounting is not, which is a state to finish rather
than a run to repeat.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from networth.backup import crypto
from networth.config import ConfigError
from networth.link_pairing import PairingError, PairingResult
from networth.link_sink import INCOMPLETE_RECOVERY, SinkError, pair_from_artifact, restore
from networth.plaid.environment import paths_for, selected_environment

SUMMARY = "Restore a sealed link-recovery artifact into this host's TokenStore (07b)."

#: What to do next, per pairing result. Operator instruction, kept out of
#: :mod:`networth.link_pairing` for the reason `classify-recovery` keeps its own
#: table out of :mod:`networth.link_crash`: the classifier must stay usable by a
#: caller that wants the verdict without this verb's opinion about the next hour.
_NEXT = {
    PairingResult.WRITTEN: (
        "Recovery is complete. The credential is in this host's TokenStore and the "
        "Link success row names the Item it bought, so the slot is counted once."
    ),
    PairingResult.ALREADY_PAIRED: (
        "Recovery is complete; the write-back had already happened, so this run "
        "changed only the credential (or nothing at all)."
    ),
    PairingResult.NO_ROW: (
        "The credential is durable and the accounting is not. This database has no "
        "Link success row for the flow -- expected when the restored backup predates "
        "the Link. KEEP the artifact and the escrowed key: they are the only record "
        "that this Item cost a lifetime slot. Nothing here can create that row, and "
        "no re-run of this verb will; once it exists, --pairing-only writes the name "
        "onto it."
    ),
    PairingResult.REFUSED: (
        "The credential is durable and the accounting is disputed -- a row exists and "
        "disagrees with this recovery. Read the reason above before touching anything; "
        "do NOT exchange again, the one-time token is spent. KEEP the artifact."
    ),
}

#: The instruction for a credential-only run, and the reason ``--pairing-only``
#: exists. A plain re-run **cannot** finish the pairing: the credential is already in
#: the ``TokenStore``, so the restore refuses before it gets there.
_FINISH_LATER = (
    "The credential is durable. The flow pairing is still only in the artifact, so 26a "
    "will count this one Link success as two lifetime slots until it lands. Finish it "
    "once this host's database is up:\n"
    "  networth restore-link-artifact --pairing-only --artifact PATH --backup-key PATH\n"
    "Do NOT re-run the full restore -- the credential is already stored and it will "
    "refuse. KEEP the artifact and the escrowed key until the pairing is applied."
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--artifact",
        metavar="PATH",
        required=True,
        help="the sealed artifact the recovery wrote. Never guessed, same as classify-recovery",
    )
    parser.add_argument(
        "--backup-key",
        metavar="PATH",
        required=True,
        help="the escrowed 03a backup key the artifact was sealed under",
    )
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument(
        "--without-database",
        action="store_true",
        help=(
            "restore the credential only, on a host whose database is not up yet. The "
            "flow pairing is then reported owed. Required explicitly: an absent "
            "database is otherwise a refusal, because treating it as 'nothing to pair' "
            "would report a wrong NETWORTH_ENV as a finished recovery"
        ),
    )
    destination.add_argument(
        "--pairing-only",
        action="store_true",
        help=(
            "apply 07b criterion 3's flow write-back and nothing else, for a recovery "
            "whose credential is already stored. The full restore cannot be re-run for "
            "this: it refuses rather than overwrite the credential it already wrote"
        ),
    )


def run(args: argparse.Namespace) -> int:
    try:
        environment = selected_environment()
        paths = paths_for(environment)
    except ConfigError as exc:
        print(f"restore-link-artifact failed: {exc}", file=sys.stderr)
        return 2

    print(f"environment   {environment.value}")
    print(f"artifact      {args.artifact}")
    if not args.pairing_only:
        print(f"token store   {paths.items}")

    if args.without_database:
        print("database      NOT consulted (--without-database)")
        return _restore(args, paths.items, connection=None)

    if not paths.database.exists():
        # **Not a fallback.** `AGENTS.md` rule 1 has the general form: a lookup that
        # falls back from one location to another is how a path bug becomes "it
        # worked on my machine". If NETWORTH_ENV or the data directory is wrong, the
        # silent version of this reports "no row for your flow, pairing owed" -- a
        # measurement about the database standing in for never having opened one.
        print(
            f"restore-link-artifact failed: no database at {paths.database}. If this "
            "host genuinely has no database yet, say so with --without-database and "
            "the flow pairing will be reported owed rather than skipped",
            file=sys.stderr,
        )
        return 2

    print(f"database      {paths.database}")
    with closing(sqlite3.connect(paths.database.as_uri() + "?mode=rw", uri=True)) as connection:
        if args.pairing_only:
            return _pair_only(args, connection)
        return _restore(args, paths.items, connection=connection)


def _pair_only(args: argparse.Namespace, connection: sqlite3.Connection) -> int:
    """Criterion 3's half on its own. Touches no credential.

    Its exit codes differ from the full restore's on purpose: nothing irreversible
    happens here, so a refusal is an ordinary ``2`` — *this did not do anything, fix
    the reason and run it again* — rather than the "already spent, do not repeat"
    status the credential path needs.
    """
    print("mode          pairing only -- the credential is not touched")
    try:
        pairing = pair_from_artifact(
            Path(args.artifact), key_file=Path(args.backup_key), connection=connection
        )
    except (SinkError, PairingError, crypto.BackupKeyError, sqlite3.Error) as exc:
        print(f"restore-link-artifact failed: {exc}", file=sys.stderr)
        print("Nothing was written. KEEP the artifact and the escrowed key", file=sys.stderr)
        return 2

    print()
    print(f"flow pairing  {pairing.result.name}")
    print(f"              {pairing.detail}")
    print()
    print(_NEXT[pairing.result])
    return 0 if pairing.paired else INCOMPLETE_RECOVERY


def _restore(
    args: argparse.Namespace, token_store: Path, *, connection: sqlite3.Connection | None
) -> int:
    try:
        outcome = restore(
            Path(args.artifact),
            key_file=Path(args.backup_key),
            token_store_directory=token_store,
            connection=connection,
        )
    except (SinkError, PairingError, crypto.BackupKeyError, sqlite3.Error) as exc:
        # Every one of these redacts itself, which is why printing `exc` is safe --
        # a property of those modules, not something this handler checked. Nothing
        # irreversible has happened on any of these paths: the artifact still holds
        # the credential, so the honest instruction is that this is repeatable.
        print(f"restore-link-artifact failed: {exc}", file=sys.stderr)
        print(
            "Nothing was consumed. The artifact still holds the credential and this "
            "verb can be re-run once the reason above is fixed",
            file=sys.stderr,
        )
        return 2

    print()
    print(f"credential    {', '.join(outcome.receipt.durable)}")
    print(f"              at {outcome.receipt.destination}")
    print(f"pairing lives {outcome.receipt.pairing.value}")
    if outcome.pairing is None:
        print("flow pairing  NOT APPLIED -- no database was consulted")
    else:
        print(f"flow pairing  {outcome.pairing.result.name}")
        print(f"              {outcome.pairing.detail}")

    print()
    if outcome.receipt.owed:
        print(f"still owed    {', '.join(outcome.receipt.owed)}")
    # `None` has no result to look up, and inventing a fifth entry for "we did not
    # look" would put an answer about the database next to four answers from it.
    print(_NEXT[outcome.pairing.result] if outcome.pairing is not None else _FINISH_LATER)
    return 0 if outcome.complete else INCOMPLETE_RECOVERY
