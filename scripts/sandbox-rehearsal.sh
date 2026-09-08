#!/usr/bin/env bash
# Run task 06's Sandbox rehearsal on the sync host, from a reviewed commit, in a
# virtualenv that does not exist before this script or after it.
#
# WHY THIS EXISTS AT ALL. Acceptance criteria 1 and 2 require executing this
# package on `tokyo-exit`: the Sandbox credential lives only at
# /etc/networth/plaid-sandbox.env and must never be copied off that host. But the
# permanent install — the unit files, the venv, the package — belongs to task 16,
# and task 16 depends on 06 through 06a and 07a. "Wait for 16" is a cycle. So 06
# brings its own way to execute, and deliberately not a durable one: nothing this
# script builds outlives it, so it cannot become the install that task 16 owns.
#
# WHAT IT REFUSES, AND WHY EACH REFUSAL IS HERE RATHER THAN IN THE CALLER:
#
#   * A ref that is not a full 40-character commit. A branch or a tag moves; what
#     was reviewed is a commit. `pip install …@main` on a host holding the Plaid
#     master credential installs whatever main says at that second.
#   * NETWORTH_ENV set to anything but sandbox. A Link is what spends a lifetime
#     Item slot (F2a), so the refusal has to land before anything is installed,
#     not inside the process that has already loaded a credential.
#   * root. The database and the token store belong to the service user; a run as
#     root leaves root-owned files in both, and the daemon that comes later
#     cannot write them.
#
# WHAT IT NEVER PRINTS. Whatever `networth rehearse-sandbox` prints, which is
# presence and type per field — no balance, no institution, no item_id, no token,
# no Plaid response body. This script adds the commit, the paths and the identity
# it ran as, and nothing else.
#
# Usage, on the host, as the service user:
#
#   ./sandbox-rehearsal.sh <40-hex-commit> [--paths-only]
#
# `--paths-only` builds the venv and asks the verb which paths the environment
# selects, then stops. It makes no Plaid call, so it is the safe first run.

set -euo pipefail

readonly REPO_URL="https://github.com/orzzzl/networth"
readonly CREDENTIAL="/etc/networth/plaid-sandbox.env"

die() {
	printf 'sandbox-rehearsal: %s\n' "$1" >&2
	exit 2
}

commit="${1:-}"
mode="${2:-}"

[ -n "$commit" ] || die "usage: $0 <40-hex-commit> [--paths-only]"
case "$commit" in
*[!0-9a-f]* | "") die "'$commit' is not a full commit id: 40 lowercase hex characters, no branch, no tag — a ref that can move is not the thing that was reviewed" ;;
esac
[ "${#commit}" -eq 40 ] || die "'$commit' is not a full commit id (40 hex characters); a short id is ambiguous and a ref that can move is not the thing that was reviewed"

case "$mode" in
"" | --paths-only) ;;
*) die "unknown argument '$mode'; the only option is --paths-only" ;;
esac

# The environment refusal, before a single byte is installed. An unset variable
# is fine — this script supplies sandbox itself — but a caller who set production
# is asking for the one thing task 06 must not do.
if [ -n "${NETWORTH_ENV:-}" ] && [ "$NETWORTH_ENV" != "sandbox" ]; then
	die "NETWORTH_ENV is '$NETWORTH_ENV'; this rehearsal runs against sandbox and nothing else (task 06, F2a)"
fi
export NETWORTH_ENV=sandbox

[ "$(id -u)" -ne 0 ] || die "refusing to run as root: the database and token store belong to the service user, and a root-owned file in either is one the daemon cannot write"

if [ "$mode" != "--paths-only" ] && [ ! -r "$CREDENTIAL" ]; then
	die "$CREDENTIAL is not readable by $(id -un); the owner installs it under the service user (task 00c) and this script reads it there, never a copy"
fi

command -v python3 >/dev/null || die "no python3 on this host"

venv="$(mktemp -d "${TMPDIR:-/tmp}/networth-rehearsal.XXXXXX")"
# On success or failure, and on an interrupt: the executable environment is the
# ephemeral part. Whatever the run wrote into the Sandbox database and token
# store is the result and stays.
trap 'rm -rf "$venv"' EXIT INT TERM

printf 'commit        %s\n' "$commit"
printf 'identity      %s (uid %s), HOME=%s\n' "$(id -un)" "$(id -u)" "${HOME:-<unset>}"
printf 'venv          %s (removed on exit)\n' "$venv"

python3 -m venv "$venv"
# --no-cache-dir keeps the claim honest: a pip cache in the service user's home
# is a durable artefact of a run that promised to leave nothing behind.
"$venv/bin/pip" install --quiet --no-cache-dir --disable-pip-version-check \
	"networth @ git+$REPO_URL@$commit"

# Prove the bytes are the reviewed ones rather than trusting the resolver: pip
# records what it actually checked out, and a full commit id must come back
# unchanged. This is the check that makes "install the reviewed commit" a fact
# about the installed tree instead of a claim about the command line.
installed="$("$venv/bin/python" - <<'PY'
import json, pathlib, sys
roots = [p for p in pathlib.Path(sys.prefix).rglob("networth-*.dist-info/direct_url.json")]
if len(roots) != 1:
    sys.exit(f"expected one networth direct_url.json, found {len(roots)}")
print(json.loads(roots[0].read_text()).get("vcs_info", {}).get("commit_id", ""))
PY
)"
[ "$installed" = "$commit" ] || die "the installed tree records commit '$installed', not '$commit'"
printf 'installed     verified at %s\n\n' "$installed"

# Deliberately not `exec`: `exec` replaces this process, and a replaced process
# runs no EXIT trap — the venv would outlive the run that promised to remove it,
# which is the one property this whole script exists to provide.
status=0
if [ "$mode" = "--paths-only" ]; then
	"$venv/bin/networth" rehearse-sandbox --print-paths-only || status=$?
else
	"$venv/bin/networth" rehearse-sandbox || status=$?
fi
exit "$status"
