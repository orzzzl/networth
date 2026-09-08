#!/usr/bin/env bash
# Run task 06's Sandbox rehearsal on the sync host, from this Mac, writing no file
# on either machine.
#
# WHY THIS IS A SCRIPT RATHER THAN A PARAGRAPH IN A PR. The first version of this
# step was shell in the PR body, and it uploaded the runner as root with
# `cat > /tmp/sandbox-rehearsal.sh`. That path is predictable, so any unprivileged
# local process on the host can create it as a symlink first and have root's
# redirection write through it — codex reproduced exactly that primitive in the PR
# #49 pre-execution review (2026-09-07). The fix is not a better temporary name:
# it is that **no file is created at all**. The runner arrives on the service
# user's stdin and is executed by a shell that reads it there.
#
# It also makes the transfer testable, which prose in a PR body never is.
#
# WHAT IT REFUSES:
#
#   * anything but a full 40-character commit — the same rule as the runner, and
#     for the same reason: what was reviewed is a commit, not a branch;
#   * an identity file it cannot read. This Mac has **no default SSH identity**,
#     so a bare `ssh`/`scp` here is not "a shorter command", it is a command that
#     fails on publickey. Every hop carries `-i` and `IdentitiesOnly=yes`;
#   * a commit that does not exist in this checkout, or one that does not carry
#     the runner. The bytes piped over are extracted from the reviewed commit with
#     `git show`, never from the working tree, so an uncommitted local edit cannot
#     ride along.
#
# Usage, on zelengs-macbook-air-2:
#
#   ./scripts/sandbox-rehearsal-remote.sh <40-hex-commit> [--paths-only]
#
# Environment (both have runbook defaults; override only for a rehearsal of the
# rehearsal):
#
#   NETWORTH_VPS_KEY     default ~/agents/secrets/networth-vps.key
#   NETWORTH_VPS_TARGET  default root@100.102.245.37
#   NETWORTH_SERVICE_USER default networth

set -euo pipefail

readonly RUNNER="scripts/sandbox-rehearsal.sh"
key="${NETWORTH_VPS_KEY:-$HOME/agents/secrets/networth-vps.key}"
target="${NETWORTH_VPS_TARGET:-root@100.102.245.37}"
service_user="${NETWORTH_SERVICE_USER:-networth}"

die() {
	printf 'sandbox-rehearsal-remote: %s\n' "$1" >&2
	exit 2
}

commit="${1:-}"
mode="${2:-}"

[ -n "$commit" ] || die "usage: $0 <40-hex-commit> [--paths-only]"
case "$commit" in
*[!0-9a-f]* | "") die "'$commit' is not a full commit id: 40 lowercase hex characters, no branch, no tag — a ref that can move is not the thing that was reviewed" ;;
esac
[ "${#commit}" -eq 40 ] || die "'$commit' is not a full commit id (40 hex characters)"

case "$mode" in
"" | --paths-only) ;;
*) die "unknown argument '$mode'; the only option is --paths-only" ;;
esac

case "$service_user" in
*[!a-z0-9_-]* | "") die "'$service_user' is not a plain user name" ;;
esac

[ -r "$key" ] || die "SSH identity '$key' is not readable; this Mac has no default identity, so there is no bare-ssh fallback to fall back to"

git rev-parse --verify --quiet "$commit^{commit}" >/dev/null ||
	die "commit $commit is not in this checkout; fetch it first — the bytes that run are extracted from it, not from the working tree"
git cat-file -e "$commit:$RUNNER" 2>/dev/null ||
	die "commit $commit does not carry $RUNNER"

printf 'commit        %s\n' "$commit"
printf 'runner        %s @ %s (piped to stdin; no file is written on either machine)\n' "$RUNNER" "$commit"
printf 'target        %s, as %s\n' "$target" "$service_user"
printf 'identity      %s\n\n' "$key"

# `bash -s` reads the program from stdin, so the runner never touches a filesystem
# on the host. `--` ends bash's own options; both arguments after it were validated
# above against a grammar with no shell metacharacter in it.
#
# `set -o pipefail` is on, and ssh is the last stage, so the status below is the
# remote exit status rather than `git show`'s.
git show "$commit:$RUNNER" |
	ssh -i "$key" \
		-o IdentitiesOnly=yes \
		-o BatchMode=yes \
		"$target" \
		"sudo -u $service_user -H bash -s -- $commit $mode"
