"""Command-line entry point.

Verbs are discovered, never registered. Every module in :mod:`networth.commands`
whose name does not start with an underscore becomes a ``networth <verb>``, and
adding one is a matter of adding one file — there is no list to edit and so no
shared file for concurrent tasks to collide in (``tasks/README.md``, task 02).

A command module must define:

``SUMMARY``
    One line of help text.
``run(args) -> int``
    The command body. Returns the process exit code.

and may define:

``add_arguments(parser) -> None``
    Called with the verb's own :class:`argparse.ArgumentParser`.
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
from collections.abc import Sequence
from types import ModuleType

from networth import commands

_REQUIRED = ("SUMMARY", "run")


def _verbs() -> dict[str, str]:
    """Map each verb to the module implementing it.

    A module named ``foo_bar.py`` is the verb ``foo-bar``: underscores do not
    look typeable in a CLI.

    Two filters keep that mapping total and injective, and both are here because
    the discovery is a *directory listing* — anyone can drop a file in, and a
    file that cannot become a verb must not be able to break the verbs that can:

    - A name that is not a Python identifier is skipped with a warning.
      ``foo-bar.py`` is such a name: :func:`pkgutil.iter_modules` lists it, but
      ``import networth.commands.foo-bar`` is not expressible, so it could only
      ever have produced a verb that fails to load. Skipping is loud rather than
      silent because the alternative is a command file that mysteriously does
      nothing.
    - A verb claimed twice is an error naming both modules. Filtering to
      identifiers already makes a collision unreachable — no identifier contains
      ``-``, so underscore-to-hyphen cannot merge two of them — but this is the
      invariant the parser depends on, and an invariant worth depending on is
      worth checking. Unchecked, the failure surfaced as
      ``argparse.ArgumentError: conflicting subparser`` on *every* invocation,
      including ``--help``: one stray file and the whole CLI is unusable.
    """
    verbs: dict[str, str] = {}
    for _, name, ispkg in pkgutil.iter_modules(commands.__path__):
        if ispkg or name.startswith("_"):
            continue
        if not name.isidentifier():
            print(
                f"networth: ignoring {name!r} in networth/commands — not an importable "
                f"module name, so it cannot be a verb; rename it to use underscores",
                file=sys.stderr,
            )
            continue
        verb = name.replace("_", "-")
        if verb in verbs:
            raise RuntimeError(
                f"two command modules claim the verb {verb!r}: "
                f"{verbs[verb]!r} and {name!r} — rename one"
            )
        verbs[verb] = name
    return verbs


def discover() -> list[str]:
    """Return the available verb names, sorted."""
    return sorted(_verbs())


def load(verb: str) -> ModuleType:
    """Import the module implementing ``verb`` and check its shape."""
    module = importlib.import_module(f"{commands.__name__}.{verb.replace('-', '_')}")
    missing = [attr for attr in _REQUIRED if not hasattr(module, attr)]
    if missing:
        raise AttributeError(
            f"command module {module.__name__!r} is missing {', '.join(missing)} — "
            f"see networth/cli.py for the contract"
        )
    return module


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="networth", description="Net-worth aggregator")
    subparsers = parser.add_subparsers(dest="verb", metavar="<verb>")
    for verb in discover():
        module = load(verb)
        sub = subparsers.add_parser(verb, help=module.SUMMARY, description=module.SUMMARY)
        add_arguments = getattr(module, "add_arguments", None)
        if add_arguments is not None:
            add_arguments(sub)
        sub.set_defaults(_run=module.run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run = getattr(args, "_run", None)
    if run is None:
        parser.print_help()
        return 2
    return int(run(args))
