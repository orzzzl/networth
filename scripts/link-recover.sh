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
# echoed, nothing is written, nothing reaches `argv` or shell history.
#
# WHERE THE CREDENTIAL LANDS (`07b`). It used to land nowhere, and that is the defect
# this task closes: the run spent the one-time `public_token`, received a long-lived
# credential, and held it in a process with nothing durable underneath it. So a sink
# is now **named on the command line** and proven writable by the verb *before* the
# prompts. It is never inferred from which argument happens to be present.
#
# From THIS script the sink is the sealed artifact, and that is a correction rather
# than a preference. The other kind writes a plain TokenStore in whatever directory
# it is given, with no transport anywhere — run from this Mac it would have put an
# unsealed access_token on this laptop while calling the destination a replacement
# host. The verb refuses it and says so. §15 is satisfied here by the encryption plus
# the escrowed key, not by declining to write; the credential reaches a real
# TokenStore through the restore, on the replacement host, where that name is true.
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

usage() {
	cat >&2 <<'USAGE'
usage: link-recover.sh <flow_id> --sink emergency-artifact --artifact PATH --backup-key PATH

The sink is required and is not guessed. This script runs on zelengs-macbook-air-2
and there is no transport from here to a replacement host, so the artifact -- one
file sealed under the already-escrowed 03a backup key -- is the destination this
command can actually reach. Restore it into a real TokenStore on the replacement
host once one is standing.

--sink replacement-host is accepted by the verb only to be refused with that
explanation; it would otherwise have written an unsealed access_token here.
USAGE
	exit 2
}

flow="${1:-}"
[ -n "$flow" ] || usage
shift
case "$flow" in
*[!0-9a-f]* | "") die "'$flow' is not a flow id: 32 lowercase hex characters, as link-start.sh printed it" ;;
esac
[ "${#flow}" -eq 32 ] || die "'$flow' is not a flow id (32 hex characters)"

# The sink arguments are checked for *presence* here and for *meaning* by the verb.
# Deliberately not re-validated: this script would then hold a second opinion about
# which combinations are legal, and the operator would meet whichever of the two is
# stricter. `_sink_from` owns that decision and refuses before the prompts.
[ "$#" -gt 0 ] || usage
case " $* " in
*" --sink "*) ;;
*) usage ;;
esac

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
exec uv run --quiet networth complete-hosted-link --from-tty --flow "$flow" --exchange "$@"
