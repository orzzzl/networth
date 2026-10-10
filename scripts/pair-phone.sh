#!/usr/bin/env bash
# Pair the phone with the daemon, or re-pair it. The owner's command, run on
# zelengs-macbook-air-2.
#
#   ./scripts/pair-phone.sh
#
# It prints a QR code and one line of text. In the app choose "Pair this phone"
# and enter that line: scan the QR code with the phone's camera, copy the text it
# shows, and paste it — or type it.
#
# WHY THE OWNER RUNS THIS AND AN AGENT DOES NOT. The line is the payload key: with
# it, anyone on the tailnet can read the published total. It must appear on the
# owner's screen and nowhere else, and an agent session keeps a transcript of
# everything it prints. So this refuses to run without a terminal.
#
# WHY SANDBOX IS NOT AN OPTION HERE. The host keeps one payload key file for
# both environments. Pairing in Sandbox would overwrite the key the phone holds,
# and the next real publication would be sealed with a key the phone never saw.
#
# Running it again re-pairs: the previous pairing stops working immediately.

set -euo pipefail

readonly KEY="$HOME/agents/secrets/networth-vps.key"
readonly HOST="root@100.102.245.37"

die() {
	printf 'pair-phone: %s\n' "$1" >&2
	exit 2
}

[ "$#" -eq 0 ] || die "this command takes no arguments"
[ -t 0 ] && [ -t 1 ] || die "run this yourself in a terminal: it prints the phone's secret pairing line, which must not be captured anywhere"
[ -r "$KEY" ] || die "$KEY is not readable; this is the key that reaches the host"

# `-t`: the remote side draws the QR code only when it has a terminal.
exec ssh -t -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
	"runuser -u networth -- env HOME=/var/lib/networth NETWORTH_ENV=production /usr/local/bin/networth pair"
