#!/usr/bin/env bash
# Mint a Sandbox Hosted Link session on the VPS and show its URL **here**, on
# zelengs-macbook-air-2 — but only after this Mac has written and read back its
# copy of the flow's recovery record.
#
# This is the driver `DESIGN.md` §19 step 2a describes and task 06a exists to
# rehearse. Its shape is the whole point:
#
#     ./scripts/sandbox-rehearsal-remote.sh <sha> --verb start-hosted-link \
#         | networth absorb-hosted-link --commit <sha>
#
# A pipe, not a variable and not a file. The `link_token` crosses from the VPS
# into the absorbing process and stops there: it is never an argument, never a
# redirect target, never echoed. The left half prints a transcript, the right
# half passes every line of it through except the one carrying the token, and
# **the URL is printed by the right half, after the record verifies.**
#
# WHY THE ORDER CANNOT BE EXPRESSED IN ONE PROCESS. §4 wants the second copy
# verified before any URL is displayed. The mint must run where the Plaid client
# secret lives, which is the VPS; a record written there would sit on the machine
# whose loss the copy exists to survive. So the two halves are on two machines
# and the second one holds the URL back. If it cannot write or cannot read back,
# it prints no URL and stops — and by **F2a** nothing has been spent, because a
# slot goes when Link *completes* and nobody has been handed a URL to complete.
#
# WHY BOTH HALVES MUST BE THE SAME COMMIT. The remote half runs bytes extracted
# from a reviewed commit (`sandbox-rehearsal-remote.sh` refuses anything else).
# The local half runs this checkout. If those differ, the transcript names a
# commit that describes only half of what ran — so this refuses unless the
# working tree is at that exact commit and clean.
#
# Usage, on zelengs-macbook-air-2:
#
#   ./scripts/link-start.sh <40-hex-commit>
#
# Environment: everything `sandbox-rehearsal-remote.sh` reads, plus
#
#   NETWORTH_LINK_RECOVERY_DIR   default ~/agents/secrets/networth-link-recovery

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

readonly REMOTE="./scripts/sandbox-rehearsal-remote.sh"

die() {
	printf 'link-start: %s\n' "$1" >&2
	exit 2
}

commit="${1:-}"
[ "$#" -le 1 ] || die "usage: $0 <40-hex-commit>; this driver takes the commit and nothing else"
[ -n "$commit" ] || die "usage: $0 <40-hex-commit>"
case "$commit" in
*[!0-9a-f]* | "") die "'$commit' is not a full commit id: 40 lowercase hex characters, no branch, no tag — a ref that can move is not the thing that was reviewed" ;;
esac
[ "${#commit}" -eq 40 ] || die "'$commit' is not a full commit id (40 hex characters)"

head="$(git rev-parse HEAD)"
[ "$head" = "$commit" ] ||
	die "this checkout is at $head, not $commit; the remote half runs the commit and the local half runs this tree, so they have to be the same bytes"
[ -z "$(git status --porcelain)" ] ||
	die "this checkout has uncommitted changes; the absorbing half would run them while the remote half runs $commit"

command -v uv >/dev/null || die "no uv on this Mac; the absorbing half runs from this checkout"

printf 'driver        %s (this Mac writes the second copy, and shows the URL)\n' "$0"
printf 'commit        %s, on both halves\n' "$commit"
printf 'recovery dir  %s\n\n' "${NETWORTH_LINK_RECOVERY_DIR:-$HOME/agents/secrets/networth-link-recovery}"

# `pipefail` alone would report a failure without saying which half, and the two
# have different consequences: a remote failure means nothing was minted, while a
# local one means a link token exists on the VPS that this Mac did not record.
# The owner needs to know which, so PIPESTATUS is read rather than `$?`.
set +e
"$REMOTE" "$commit" --verb start-hosted-link |
	uv run --quiet networth absorb-hosted-link --commit "$commit"
statuses=("${PIPESTATUS[@]}")
set -e

remote_status="${statuses[0]}"
local_status="${statuses[1]}"

if [ "$remote_status" -ne 0 ]; then
	printf '\nlink-start: the mint half exited %s. Nothing was minted or the mint refused; no URL, nothing spent (F2a)\n' "$remote_status" >&2
	exit "$remote_status"
fi
if [ "$local_status" -ne 0 ]; then
	printf '\nlink-start: the mint succeeded and this Mac did not record it (exit %s).\n' "$local_status" >&2
	printf 'No URL was printed, so no slot can be spent through it (F2a). The link token on the VPS expires on Plaid'"'"'s clock; mint again once this Mac can write its copy.\n' >&2
	exit "$local_status"
fi
