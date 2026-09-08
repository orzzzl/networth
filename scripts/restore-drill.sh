#!/usr/bin/env bash
# Runs on zelengs-macbook-air-2 against its own pulled archive. Verification is
# offline; an unavailable VPS only defers the separate record-drill write-back.
set -euo pipefail
exec networth backup restore-drill "$@"
