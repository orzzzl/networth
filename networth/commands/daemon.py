"""Daemon jobs selected by the host's systemd units."""

import argparse

from networth.runtime import run as run

SUMMARY = "Run a scheduled host job (no Link creation or interactive secrets)."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("job", choices=("init", "sync", "publish", "archive", "link", "link-flow"))
    parser.add_argument("--flow", help=argparse.SUPPRESS)
    parser.add_argument("--country-code", action="append", default=None)
