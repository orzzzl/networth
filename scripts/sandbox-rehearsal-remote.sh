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
#   ./scripts/sandbox-rehearsal-remote.sh <40-hex-commit> [--paths-only] \
#       [--verb <name>] [--flow <id>] [--link-mode <name>]
#
# `--flow` and `--link-mode` exist for `complete-hosted-link` and are refused for
# every other verb. They are the whole of task 06a's second half reaching the
# reviewed-commit path: without them that verb is runnable only by hand, which is
# the defect this transport was written to remove.
#
# Environment (both have runbook defaults; override only for a rehearsal of the
# rehearsal):
#
#   NETWORTH_VPS_KEY     default ~/agents/secrets/networth-vps.key
#   NETWORTH_VPS_TARGET  default root@100.102.245.37
#   NETWORTH_SERVICE_USER default networth

set -euo pipefail

readonly RUNNER="scripts/sandbox-rehearsal.sh"
readonly DEFAULT_VERB="rehearse-sandbox"
readonly ALLOWED_VERBS="rehearse-sandbox probe-hosted-link start-hosted-link complete-hosted-link"
# Plain words, not option spellings: the flag is built from the word that matched.
readonly ALLOWED_LINK_MODES="retrieve-only exchange exchange-twice"
key="${NETWORTH_VPS_KEY:-$HOME/agents/secrets/networth-vps.key}"
target="${NETWORTH_VPS_TARGET:-root@100.102.245.37}"
service_user="${NETWORTH_SERVICE_USER:-networth}"

die() {
	printf 'sandbox-rehearsal-remote: %s\n' "$1" >&2
	exit 2
}

commit="${1:-}"
shift || true
mode=""
verb="$DEFAULT_VERB"
flow=""
link_mode=""

[ -n "$commit" ] || die "usage: $0 <40-hex-commit> [--paths-only] [--verb <name>] [--flow <id>] [--link-mode <name>]"
case "$commit" in
*[!0-9a-f]* | "") die "'$commit' is not a full commit id: 40 lowercase hex characters, no branch, no tag — a ref that can move is not the thing that was reviewed" ;;
esac
[ "${#commit}" -eq 40 ] || die "'$commit' is not a full commit id (40 hex characters)"

while [ "$#" -gt 0 ]; do
	case "$1" in
	--paths-only) mode="--paths-only" ;;
	--verb)
		shift || true
		verb="${1:-}"
		[ -n "$verb" ] || die "--verb needs a name; the allow-list is: $ALLOWED_VERBS"
		;;
	--flow)
		shift || true
		flow="${1:-}"
		[ -n "$flow" ] || die "--flow needs the id start-hosted-link printed"
		;;
	--link-mode)
		shift || true
		link_mode="${1:-}"
		[ -n "$link_mode" ] || die "--link-mode needs a name; the allow-list is: $ALLOWED_LINK_MODES"
		;;
	*) die "unknown argument '$1'; the options are --paths-only, --verb <name>, --flow <id> and --link-mode <name>" ;;
	esac
	shift || true
done

# Checked here as well as in the runner, and not because the runner's check is in
# doubt. This value is interpolated into the command string sent over ssh, so it
# is validated on the side that does the interpolating — a caller who mistypes a
# verb finds out locally, before a connection is opened to the host holding the
# Plaid master credential.
case " $ALLOWED_VERBS " in
*" $verb "*) ;;
*) die "'$verb' is not a verb this runner may execute; the allow-list is: $ALLOWED_VERBS" ;;
esac

# Both of `complete-hosted-link`'s arguments are interpolated into the command
# string below, so both are validated here for the same reason the verb is — and
# the runner checks them again on its own side. A `flow_id` is
# `uuid.uuid4().hex` (`tokenstore.new_flow_id`): 32 lowercase hex characters, the
# same shape of check as the 40 the commit gets, never a hunt for characters a
# shell might find interesting.
if [ -n "$flow" ]; then
	case "$flow" in
	*[!0-9a-f]* | "") die "'$flow' is not a flow id: 32 lowercase hex characters, as start-hosted-link printed it" ;;
	esac
	[ "${#flow}" -eq 32 ] || die "'$flow' is not a flow id (32 hex characters)"
fi

if [ -n "$link_mode" ]; then
	case " $ALLOWED_LINK_MODES " in
	*" $link_mode "*) ;;
	*) die "'$link_mode' is not a link mode; the allow-list is: $ALLOWED_LINK_MODES" ;;
	esac
fi

# Refused before a connection is opened to the host holding the Plaid master
# credential — the same placement, and the same reason, as the verb check above.
if [ "$verb" = "complete-hosted-link" ]; then
	[ "$mode" != "--paths-only" ] || die "complete-hosted-link has no --paths-only form; the paths it would print are the ones start-hosted-link --paths-only already prints for this environment"
	[ -n "$flow" ] || die "complete-hosted-link needs --flow <id>: the flow id start-hosted-link printed"
	[ -n "$link_mode" ] || die "complete-hosted-link needs --link-mode <name>; the allow-list is: $ALLOWED_LINK_MODES. There is no default: a default that exchanges is a default that spends"
elif [ -n "$flow" ] || [ -n "$link_mode" ]; then
	die "--flow and --link-mode mean something only to complete-hosted-link, and '$verb' would silently ignore them"
fi

# WHERE THE TOKEN'S DESTINATION IS DECIDED, AND WHY IT IS HERE.
# `start-hosted-link` writes a `link_token` to stdout for `absorb-hosted-link` to
# take off the pipe. **Only this side can see where that stdout goes.** sshd hands
# the remote child a pipe whichever local destination the transcript ends in, so
# the verb's own check cannot tell `| networth absorb-hosted-link` from
# `> mint.log`; by the time those bytes are distinguishable they are already on a
# disk. The verb still refuses a terminal — that refusal is about display and
# stays where it is — but a file is refused here, before `ssh` opens a connection
# to the host holding the Plaid master credential, and therefore before anything
# is minted. Nothing spent, by F2a, because nothing exists to spend.
#
# Measured on this Mac rather than assumed: `| cat` makes fd 1 a FIFO (`-p`),
# `> mint.log` a regular file (`-f`), and `> /dev/null` a character device (`-c`).
# Requiring the FIFO is therefore the one condition that admits the supported
# caller and nothing else — a file, a terminal and a discard all fail it. That is
# deliberate: `scripts/link-start.sh` is the only destination that consumes the
# token, and any other is either keeping it or losing it.
#
# `--paths-only` is exempt because it returns before the mint and prints paths.
if [ "$verb" = "start-hosted-link" ] && [ "$mode" != "--paths-only" ]; then
	[ -p /dev/fd/1 ] ||
		die "this verb writes a link token to stdout and stdout is not a pipe. Run it through scripts/link-start.sh, which pipes it into 'networth absorb-hosted-link' on this Mac; redirecting it into a file would put a spendable credential on disk. Refused here rather than on the host, because the host is handed a pipe either way and cannot tell the difference"
fi

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
printf 'verb          networth %s\n' "$verb"
# The transcript is the artefact that outlives the run, and for this verb the
# mode is the difference between a measurement and a spent `public_token`. It is
# printed rather than left implicit for the same reason the non-default origin is.
[ -z "$link_mode" ] || printf 'flow          %s, --%s\n' "$flow" "$link_mode"
printf 'target        %s, as %s\n' "$target" "$service_user"
printf 'identity      %s\n\n' "$key"

# `bash -s` reads the program from stdin, so the runner never touches a filesystem
# on the host. `--` ends bash's own options; every word after it was validated
# above against a grammar with no shell metacharacter in it — the commit against
# 40 hex characters, the mode against one literal, the verb against a four-word
# allow-list, the flow id against 32 hex characters, and the link mode against a
# three-word allow-list. `$mode` expands to nothing when unset, which is why the
# remote command tolerates the gap rather than needing a conditional.
#
# The link arguments are appended only when they exist, rather than expanded from
# an empty variable: an invocation that does not use them is the same bytes on the
# wire it was before this verb existed, which is what the transport tests read.
#
# `set -o pipefail` is on, and ssh is the last stage, so the status below is the
# remote exit status rather than `git show`'s.
remote="sudo -u $service_user -H bash -s -- $commit $mode --verb $verb"
[ -z "$link_mode" ] || remote="$remote --flow $flow --link-mode $link_mode"

git show "$commit:$RUNNER" |
	ssh -i "$key" \
		-o IdentitiesOnly=yes \
		-o BatchMode=yes \
		"$target" \
		"$remote"
