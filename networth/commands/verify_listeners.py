"""Verify task 20's listener invariant on the live host."""

from __future__ import annotations

import argparse
import sys

from networth.listeners import ListenerCheckError, verify_live_listener_surface

SUMMARY = "Verify networth's tailnet bind and the host's approved public-listener baseline."


def run(_args: argparse.Namespace) -> int:
    try:
        surface = verify_live_listener_surface()
    except ListenerCheckError as exc:
        print(f"listener verification failed: {exc}", file=sys.stderr)
        return 2
    print(f"networth listener  {surface.networth.description}")
    print("public baseline    unchanged")
    return 0
