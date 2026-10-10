#!/usr/bin/env bash
# Connect one institution. The owner's command, run on zelengs-macbook-air-2.
#
#   ./scripts/link.sh                  connect one institution (Production)
#   ./scripts/link.sh --resume <flow>  show an already-minted page again
#   ./scripts/link.sh --status <flow>  say where a flow stands, and stop
#   ./scripts/link.sh --sandbox        the rehearsal; spends nothing
#
# This is the only path that spends one of the ten lifetime Plaid Items, so it is
# the owner's to run: he types bank credentials and MFA into Plaid's page, and no
# agent may do that or automate it (AGENTS.md rule 3).
#
# Everything it decides is in `networth owner-link` (networth/owner_link.py),
# where it is tested. This file only chooses the environment and finds the
# checkout, so that the command works from any directory.
#
# Production is the default and the flag is for the rehearsal, deliberately that
# way round: the owner should never have to remember a flag to do the real thing,
# and Production asks for a typed confirmation at a terminal before it creates
# anything.

set -euo pipefail

die() {
	printf 'link: %s\n' "$1" >&2
	exit 2
}

usage='usage: link.sh [--sandbox] [--resume <flow> | --status <flow>] [--no-open] [--no-watch]'

environment=production
arguments=()
while [ "$#" -gt 0 ]; do
	case "$1" in
	--sandbox) environment=sandbox ;;
	--resume | --status)
		[ "$#" -ge 2 ] || die "$1 needs the flow id. $usage"
		arguments+=("$1" "$2")
		shift
		;;
	--no-open | --no-watch) arguments+=("$1") ;;
	*) die "unexpected argument '$1'. $usage" ;;
	esac
	shift
done

cd "$(dirname "${BASH_SOURCE[0]}")"
cd "$(git rev-parse --show-toplevel)"
command -v uv >/dev/null || die "no uv on this Mac; this command runs from its own checkout"

exec uv run --quiet networth owner-link --environment "$environment" ${arguments[@]+"${arguments[@]}"}
