#!/usr/bin/env bash
# Retrieve and exchange a finished Hosted Link session **from this Mac**, using
# the recovery record this machine already holds.
#
# On zelengs-macbook-air-2:
#
#   ./scripts/link-recover.sh <flow_id>
#
# This is task 06a's measurement (iv) — "does retrieval and exchange work from a
# second host?" — and it is deliberately the **first form of the command `DESIGN.md`
# §19 step 2a names**, not a throwaway that resembles it. §19's procedure is already
# written: *"it already holds the recovery record and the `link_token`; it will
# prompt you for `client_id` and the secret"*. Two prompts, and the token comes from
# the record. Task `07b` extends this same script for the real emergency; if (iv)
# had rehearsed some other call path, the emergency would inherit a path nothing had
# ever run.
#
# WHAT IT ASKS FOR AND WHY IT IS NOT MORE. `/link/token/get` needs `client_id`,
# `secret` and the `link_token`. This Mac holds the third — that is the whole of
# §4's second copy — and must not hold the first two (§15), so they are typed at a
# prompt that reads **the controlling terminal** and refuses a pipe. Nothing is
# echoed, nothing is written, nothing reaches `argv` or shell history, and nothing
# is persisted by the run: measurement (iv) asks whether retrieval and exchange
# *succeed* from another host, and a run that stored an `access_token` here would
# answer the question and widen the laptop while doing it.
#
# SANDBOX ONLY, IN THIS FORM. The verb refuses any other environment before a
# credential is read, and this script pins `NETWORTH_ENV` rather than inheriting it:
# a rehearsal that is one exported variable away from Production is not a rehearsal.
# `07b` is where this widens, with the Production refusals that belong to it.

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

die() {
	printf 'link-recover: %s\n' "$1" >&2
	exit 2
}

flow="${1:-}"
[ "$#" -le 1 ] || die "usage: $0 <flow_id>; this takes the flow id and nothing else"
[ -n "$flow" ] || die "usage: $0 <flow_id> — the id link-start.sh printed"
case "$flow" in
*[!0-9a-f]* | "") die "'$flow' is not a flow id: 32 lowercase hex characters, as link-start.sh printed it" ;;
esac
[ "${#flow}" -eq 32 ] || die "'$flow' is not a flow id (32 hex characters)"

if [ -n "${NETWORTH_ENV:-}" ] && [ "$NETWORTH_ENV" != "sandbox" ]; then
	die "NETWORTH_ENV is '$NETWORTH_ENV'; this form of the command runs against sandbox and nothing else (task 06a). Production recovery is 07b"
fi
export NETWORTH_ENV=sandbox

command -v uv >/dev/null || die "no uv on this Mac; this runs the verb from this checkout"

# Before the record is read and before the owner is asked for anything. Measurement
# (iv) is "does retrieval and exchange work **from the second host**", so a run on
# some other machine does not produce a weaker version of that answer — it produces
# a wrong one, recorded as evidence. Refusing here also means the two prompts never
# happen on a machine that had no business collecting them.
#
# `hostname` is printed below for the transcript and is NOT the check: this Mac
# answers `Zelengs-MacBook-Air.local`, which does not distinguish the owner's Airs
# from each other. The address does; see `networth/mac_identity.py`.
uv run --quiet networth verify-this-mac ||
	die "this Mac is not the second host measurement (iv) is about (the refusal above says which address it looked for). Nothing was read and nothing was prompted for"

printf 'host          %s (measurement (iv): the VPS takes no part in either call)\n' "$(hostname)"
printf 'flow          %s\n' "$flow"
printf 'recovery dir  %s\n' "${NETWORTH_LINK_RECOVERY_DIR:-$HOME/agents/secrets/networth-link-recovery}"
printf 'prompts       client_id and the sandbox secret, on this terminal; the link token comes from the record\n\n'

# `--exchange` rather than a choice. In the emergency this script is the first form
# of, there is exactly one thing to do — retrieve, then exchange — and an operator
# reading it against a 30-minute clock should not be picking a mode. The measurement
# form is the same shape for the same reason.
exec uv run --quiet networth complete-hosted-link --from-tty --flow "$flow" --exchange
