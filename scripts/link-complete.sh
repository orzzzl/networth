#!/usr/bin/env bash
# Finish a Sandbox Hosted Link flow on the VPS and retire this Mac's copy of its
# recovery record **here**, on zelengs-macbook-air-2 — but only after the remote
# half reports that flow EXCHANGED.
#
# The completion counterpart of `scripts/link-start.sh`, and the same shape:
#
#     ./scripts/sandbox-rehearsal-remote.sh <sha> --verb complete-hosted-link \
#         --flow <id> --link-mode <mode> \
#         | networth retire-hosted-link --flow <id> [--expect-completion]
#
# WHY A DRIVER AND NOT JUST THE REMOTE SCRIPT. `DESIGN.md` §4 gives the
# interactive Mac driver the job of deleting the second copy as soon as the flow
# reports EXCHANGED, with the puller's expiry sweep covering only the crash gap.
# The exchange runs where the Plaid client secret is, which is the VPS; the
# record is a file on this Mac. Until PR #75's re-review the delete sat inside the
# VPS verb, where it found an empty directory, said nothing, and left every
# completed flow's record on the laptop for seven hours — the normal path taking
# the crash backstop, silently.
#
# WHY THE MARKER AND NOT THE EXIT STATUS. Zero from the remote half means "that
# process did not fail". `--link-mode retrieve-only` exits zero and leaves the
# flow deliberately live; so does a poll that finds no public_token yet. Those are
# exactly the states where the record is the only way back, so the deletion waits
# for a positive report naming this flow. Absence means keep.
#
# WHY BOTH HALVES MUST BE THE SAME COMMIT: see `link-start.sh`. Same reason, same
# refusal.
#
# Usage, on zelengs-macbook-air-2:
#
#   ./scripts/link-complete.sh <40-hex-commit> --flow <id> --link-mode <mode>
#
#     --link-mode retrieve-only   poll and stop; nothing is exchanged, nothing retired
#     --link-mode exchange        exchange every public_token, then retire the record
#     --link-mode exchange-twice  exchange, exchange again, probe the first, then retire
#
# Environment: everything `sandbox-rehearsal-remote.sh` reads, plus
#
#   NETWORTH_LINK_RECOVERY_DIR   default ~/agents/secrets/networth-link-recovery

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

readonly REMOTE="./scripts/sandbox-rehearsal-remote.sh"

die() {
	printf 'link-complete: %s\n' "$1" >&2
	exit 2
}

commit="${1:-}"
[ -n "$commit" ] || die "usage: $0 <40-hex-commit> --flow <id> --link-mode <mode>"
shift
case "$commit" in
*[!0-9a-f]* | "") die "'$commit' is not a full commit id: 40 lowercase hex characters, no branch, no tag — a ref that can move is not the thing that was reviewed" ;;
esac
[ "${#commit}" -eq 40 ] || die "'$commit' is not a full commit id (40 hex characters)"

flow=""
link_mode=""
while [ "$#" -gt 0 ]; do
	case "$1" in
	--flow)
		[ "$#" -ge 2 ] || die "--flow needs a value"
		flow="$2"
		shift 2
		;;
	--link-mode)
		[ "$#" -ge 2 ] || die "--link-mode needs a value"
		link_mode="$2"
		shift 2
		;;
	*) die "unexpected argument '$1'; usage: $0 <40-hex-commit> --flow <id> --link-mode <mode>" ;;
	esac
done

[ -n "$flow" ] || die "--flow is required: this driver retires one named record and never 'the current one'"
case "$flow" in
*[!0-9a-f]* | "") die "'$flow' is not a flow id (32 lowercase hex characters)" ;;
esac
[ "${#flow}" -eq 32 ] || die "'$flow' is not a flow id (32 lowercase hex characters)"

# Enumerated here as well as in the verb, because this half decides whether a
# missing EXCHANGED marker is a failure or the expected ending, and that decision
# cannot be made from a mode it does not recognise.
case "$link_mode" in
exchange | exchange-twice) expect=(--expect-completion) ;;
retrieve-only) expect=() ;;
"") die "--link-mode is required and never defaulted: a default that exchanges is a default that spends" ;;
*) die "unknown --link-mode '$link_mode'; one of retrieve-only, exchange, exchange-twice" ;;
esac

head="$(git rev-parse HEAD)"
[ "$head" = "$commit" ] ||
	die "this checkout is at $head, not $commit; the remote half runs the commit and the local half runs this tree, so they have to be the same bytes"
[ -z "$(git status --porcelain)" ] ||
	die "this checkout has uncommitted changes; the retiring half would run them while the remote half runs $commit"

command -v uv >/dev/null || die "no uv on this Mac; the retiring half runs from this checkout"

# Asked before the exchange rather than after it. The retiring half checks the
# same thing before it unlinks anything — that is the check that matters — but a
# refusal here happens while there is still nothing to be sorry about, rather than
# after a slot has been spent on a machine that then cannot clean up after itself.
uv run --quiet networth verify-this-mac ||
	die "this Mac is not the machine the second copy belongs on (the refusal above says which address it looked for). Nothing was exchanged"

printf 'driver        %s (the VPS exchanges; this Mac retires its own copy)\n' "$0"
printf 'commit        %s, on both halves\n' "$commit"
printf 'flow          %s\n' "$flow"
printf 'link mode     %s\n' "$link_mode"
printf 'recovery dir  %s\n\n' "${NETWORTH_LINK_RECOVERY_DIR:-$HOME/agents/secrets/networth-link-recovery}"

# What the retiring half did with the record, reported by the half that did it.
#
# The first version of this driver inferred it from the remote's exit status, and
# the inference was wrong in a reachable case: `complete-hosted-link` prints the
# EXCHANGED marker *before* `--exchange-twice` runs its second exchange and its
# first-token probe, so a nonzero remote status can arrive after the record has
# already, correctly, been retired. The driver then said "this Mac kept its
# recovery record" about a file it had just deleted. A post-marker ssh or runner
# failure has the same shape.
#
# The deletion itself was right — the marker is printed only after the exchange
# landed and any credential was stored, and an exchanged flow cannot be stranded,
# so the record is residue from that instant. Only the report was wrong. So the
# report now comes from the process that acted instead of from a status that
# cannot see it.
outcome_file="$(mktemp -t networth-link-complete)"
cleanup() { rm -f "$outcome_file"; }
trap cleanup EXIT

# PIPESTATUS rather than `$?`, for the reason `link-start.sh` gives: the two halves
# fail with different consequences, and neither status alone describes the record.
set +e
"$REMOTE" "$commit" --verb complete-hosted-link --flow "$flow" --link-mode "$link_mode" |
	uv run --quiet networth retire-hosted-link --flow "$flow" \
		--outcome-file "$outcome_file" ${expect[@]+"${expect[@]}"}
statuses=("${PIPESTATUS[@]}")
set -e

remote_status="${statuses[0]}"
local_status="${statuses[1]}"

# Read rather than assumed, and "the file is not there" is its own answer: the
# retiring half writes this last, so its absence means that half never got to
# speak and this driver must not claim to know what it did.
record_outcome="$(cat "$outcome_file" 2>/dev/null)"

if [ "$remote_status" -ne 0 ]; then
	case "$record_outcome" in
	retired)
		printf '\nlink-complete: the remote half exited %s AFTER reporting the exchange.\n' "$remote_status" >&2
		printf 'The exchange landed and this Mac retired its recovery record, which is correct — an exchanged flow cannot be stranded, so the record was residue from the moment the marker was printed. What failed is whatever the remote did next: with --link-mode exchange-twice that is the second exchange or the first-token probe. Nothing is stranded and nothing needs recovering; read the remote transcript above for the measurement that did not complete.\n' >&2
		;;
	kept)
		printf '\nlink-complete: the remote half exited %s. This Mac kept its recovery record, which is the only way back to the flow if it is still live\n' "$remote_status" >&2
		;;
	*)
		printf '\nlink-complete: the remote half exited %s, and the retiring half did not report what it did with the record.\n' "$remote_status" >&2
		printf 'This driver will not guess. Look in %s for %s.json: if it is there the flow may still be live and that file is the way back; if it is gone the exchange had already been reported.\n' "${NETWORTH_LINK_RECOVERY_DIR:-$HOME/agents/secrets/networth-link-recovery}" "$flow" >&2
		;;
	esac
	exit "$remote_status"
fi
if [ "$local_status" -ne 0 ]; then
	printf '\nlink-complete: the remote half finished and this Mac did not retire its record (exit %s).\n' "$local_status" >&2
	printf 'The reason is above. If the exchange landed, the record is inert — it holds no Plaid client credential — and the puller removes it once reap_after has passed.\n' >&2
	exit "$local_status"
fi
