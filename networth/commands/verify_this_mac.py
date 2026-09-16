"""Refuse to continue unless this is the machine the second copy belongs on.

The drivers need this answer *before* they do the thing that cannot be undone —
`scripts/link-start.sh` before it mints, `scripts/link-recover.sh` before it
reads a record or prompts for a credential. Both run on the Mac and both would
otherwise discover the problem only after the expensive half.

It is a verb rather than a few lines of shell in each script because the
discriminator must not be duplicated: one pinned address, measured one way. See
:mod:`networth.mac_identity` for why the address and not the hostname.

Makes no Plaid call, reads no credential, writes nothing.
"""

from __future__ import annotations

import argparse
import sys

from networth import mac_identity

SUMMARY = "check that this machine is the one recovery records may be written on"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing on success; the exit status is the answer",
    )


def run(args: argparse.Namespace) -> int:
    try:
        verified = mac_identity.verify()
    except mac_identity.WrongHost as exc:
        print(f"verify-this-mac: {exc}", file=sys.stderr)
        return 2
    if not args.quiet:
        print(
            f"host          {verified} "
            f"(holds {mac_identity.REQUIRED_ADDRESS}; measured, not assumed)"
        )
    return 0
