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
from collections.abc import Sequence
from types import ModuleType

from networth import commands

_REQUIRED = ("SUMMARY", "run")


def discover() -> list[str]:
    """Return the available verb names, sorted.

    A module named ``foo_bar.py`` is the verb ``foo-bar``: underscores are not
    typeable-looking in a CLI, and the mapping is one-way so two modules can
    never claim the same verb.
    """
    return sorted(
        name.replace("_", "-")
        for _, name, ispkg in pkgutil.iter_modules(commands.__path__)
        if not ispkg and not name.startswith("_")
    )


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
