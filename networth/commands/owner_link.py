"""``networth owner-link`` — connect one institution, from the owner's Mac (task 08).

The command behind ``scripts/link.sh``. The flow and its reasoning are in
:mod:`networth.owner_link`; this module supplies the three things that only
exist on the real machine — a terminal, the backup commands, and a browser — so
the flow itself can be exercised with none of them.

**Production asks for a typed confirmation at a terminal, and that is the gate
an agent cannot pass.** Agents hold the same SSH key this command uses, so
nothing here can *prevent* one from minting. What it can do is make the owner's
keystroke the thing that proceeds: an agent session has no terminal on stdin,
and Production refuses without one.

**Sandbox is the rehearsal, and it is the same code.** It skips exactly the
steps that have no Sandbox counterpart: the unit check (the installed services
run Production), the two backup steps (the backup configuration is Production's)
and the confirmation (nothing lifetime is spent). Everything that can fail
before a URL is shown is the path the owner will run.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from networth import link_recovery, owner_link
from networth.plaid.environment import PlaidEnvironment

SUMMARY = "Connect one institution: the owner's Link driver, run on zelengs-macbook-air-2."

VPS_ADDRESS = "100.102.245.37"


class Terminal:
    def say(self, line: str = "") -> None:
        print(line, flush=True)

    def ask(self, prompt: str) -> str:
        try:
            return input(prompt)
        except EOFError:
            return ""

    def interactive(self) -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()


def _backup(*arguments: str, timeout: float) -> None:
    """Run one Mac-side backup action from this same checkout, showing nothing.

    The action's own output is a JSON summary with an archive id; a failure is
    reported by the caller as the exception type, never as captured text.
    """
    subprocess.run(  # noqa: S603 - fixed executable and arguments
        [sys.executable, "-m", "networth", "backup", *arguments, "--ssh-host", VPS_ADDRESS],
        check=True,
        capture_output=True,
        timeout=timeout,
    )


def _canary() -> None:
    # Probe build, pull, decrypt, delete — over the restricted key and its
    # dispatcher, which is the route a real backup takes.
    _backup("canary", timeout=300)


def _protect(remote: owner_link.Remote) -> None:
    # Built now rather than left to the archive timer, so the archive that is
    # pulled is one that contains the credential that was just stored.
    remote.verb("backup", "build-archive", "current", stated=False)
    _backup("pull", timeout=600)


def _open(url: str) -> None:
    subprocess.run(["open", url], check=False, capture_output=True)  # noqa: S603, S607


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--environment",
        required=True,
        choices=[environment.value for environment in PlaidEnvironment],
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", metavar="FLOW", help="show an existing flow's page again")
    mode.add_argument("--status", metavar="FLOW", help="report where a flow stands, and stop")
    parser.add_argument("--no-open", action="store_true", help="print the URL without opening it")
    parser.add_argument("--no-watch", action="store_true", help="stop once the URL is shown")


def run(args: argparse.Namespace) -> int:
    environment = PlaidEnvironment(args.environment)
    production = environment is PlaidEnvironment.PRODUCTION
    remote = owner_link.SshRemote(environment)
    console = Terminal()
    if args.status is not None:
        return owner_link.report_status(remote, console, args.status, environment=environment)
    return owner_link.run_link(
        remote,
        console,
        environment=environment,
        directory=link_recovery.mac_recovery_directory(),
        resume=args.resume,
        watch=not args.no_watch,
        canary=_canary if production else None,
        protect=(lambda: _protect(remote)) if production else None,
        open_url=None if args.no_open else _open,
    )
