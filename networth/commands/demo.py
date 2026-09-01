"""Proof that a new file in this directory is a working verb.

This module exists so the auto-discovery contract has a test that fails loudly
if the mechanism regresses (task 02's collision-avoidance criterion). It reads
nothing, writes nothing and contacts no network service.
"""

from __future__ import annotations

import argparse

SUMMARY = "Print a line proving command auto-discovery works."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", default="world", help="who to greet")


def run(args: argparse.Namespace) -> int:
    print(f"networth demo: discovered, hello {args.name}")
    return 0
