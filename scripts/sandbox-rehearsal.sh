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
# WHAT "THE REVIEWED COMMIT" MEANS HERE, AND WHY IT TOOK TWO TRIES. The first
# version installed `networth @ git+<url>@<sha>` and then read pip's
# `direct_url.json` back to prove the commit. That proves which *source* commit
# was checked out and nothing else: pip had already resolved, downloaded, built
# and executed a dependency tree by then. `pyproject.toml` has open lower bounds
# (`plaid-python>=43.0.0`, `urllib3>=2.7.0`), so a new SDK release, a new
# transitive dependency or a different build backend all change what the
# "reviewed" commit executes, and none of them are the reviewed commit. (Codex
# caught this in the PR #49 pre-execution review, 2026-09-07.) So now:
#
#   * the source arrives over `git`, which verifies object hashes for us, and
#     HEAD is asserted to be the requested commit;
#   * the dependencies are the exact set in the reviewed `uv.lock` — exported to
#     `requirements-{build,runtime}.txt`, every distribution pinned with `==` and
#     a sha256, installed with `--require-hashes --no-deps` so pip resolves
#     nothing and cannot fetch anything the lock does not name;
#   * `plaid-python` ships an sdist and no wheel, so a build backend really does
#     execute on this host. `--no-build-isolation` over a hash-pinned setuptools
#     is what keeps that backend inside the pinned set instead of being fetched
#     unpinned by pip at build time;
#   * and this package itself is never built at all. It runs from the verified
#     checkout over `PYTHONPATH`, which removes `hatchling` — the last unpinned
#     participant — from the execution path entirely.
#
# WHAT IT REFUSES, AND WHY EACH REFUSAL IS HERE RATHER THAN IN THE CALLER:
#
#   * A ref that is not a full 40-character commit. A branch or a tag moves; what
#     was reviewed is a commit. Installing `@main` on a host holding the Plaid
#     master credential installs whatever main says at that second.
#   * NETWORTH_ENV set to anything but sandbox. A Link is what spends a lifetime
#     Item slot (F2a), so the refusal has to land before anything is installed,
#     not inside the process that has already loaded a credential.
#   * root. The database and the token store belong to the service user; a run as
#     root leaves root-owned files in both, and the daemon that comes later
#     cannot write them.
#   * A checkout whose requirement files are missing, or carry a requirement that
#     is not `==`-pinned with a hash. `--require-hashes` would refuse those too;
#     this refuses them by name, before a network fetch, and is what the offline
#     tests drive.
#
# WHAT IT NEVER PRINTS. Whatever the verb prints, which is presence and type per
# field — no balance, no institution, no item_id, no token, no Plaid response
# body. This script adds the commit, the origin, the paths, the verb and the
# identity it ran as, and nothing else.
#
# **A HOSTED URL REACHES A TRANSCRIPT THROUGH EXACTLY ONE VERB, AND THAT IS NEW.**
# A hosted URL is openable by whoever holds it, and finishing Link through it
# spends a lifetime Item slot (F2a). Every transcript this script produces is an
# artefact that outlives the run and gets attached to a PR. Until `start-hosted-link`
# there were two independent refusals and no way through them:
#
#   1. `probe-hosted-link` **has no option that prints the URL** — the verb's
#      parser does not define one, and a test asserts its absence; and
#   2. this script forwarded no such flag, and its allow-list admitted only the
#      verb name itself.
#
# **`start-hosted-link` is the deliberate widening this comment told the next
# editor to make here rather than in advance**, and it is made in the change that
# needs it (task 06a, session 1). It is the narrowest shape that works:
#
#   - the widening is *one allow-list entry*, not a flag. There is still no option
#     anywhere that turns a URL on for a verb that does not print one, so refusal
#     (1) is untouched for `probe-hosted-link` and `rehearse-sandbox`, and a test
#     still asserts it;
#   - it is **Sandbox-only** — `start-hosted-link` refuses any other environment
#     before the credential is read, so nothing that reaches a transcript here can
#     spend anything counting against the ten lifetime slots; and
#   - the verb makes the `link_token` durable *before* it emits anything.
#
# **AND THE URL IS NO LONGER WHAT REACHES THIS TRANSCRIPT — A `link_token` IS.**
# *(Task 06a, PR #75 review, option A.)* §4 requires the second copy of the
# recovery record to be on `zelengs-macbook-air-2` and read back before any URL is
# shown, and a verb running here cannot satisfy that. So `start-hosted-link` no
# longer prints a URL at all: it emits one marked line carrying the mint result,
# `scripts/link-start.sh` pipes this transcript into `networth absorb-hosted-link`
# on the Mac, and that verb writes the copy, verifies it, and prints the URL.
#
# **What that changes for anyone attaching a transcript to a PR**: the sensitive
# line is no longer an openable URL, it is a credential. The verb refuses to run
# with a terminal on stdout, and the driver consumes the line rather than showing
# it. *(Until the PR #75 re-review this paragraph ended by recording a residual —
# that redirecting this script's stdout to a file would still capture the line.
# It would, and nothing on this machine could have noticed: sshd hands this
# process a pipe whether the Mac end is the absorber or `> mint.log`. The check
# therefore had to go where the destination is observable, and now lives in
# `scripts/sandbox-rehearsal-remote.sh`, which requires fd 1 to be a FIFO before
# it opens the connection. The residual is closed, on the only side that could
# close it.)* `scripts/link-start.sh` is the only supported caller for this verb,
# and it is the one that makes the line go nowhere.
#
# What a reader must not conclude from this paragraph: that the refusals were
# soft. They were not, and the Production half of them is unchanged — task 08
# mints Production URLs, run by the owner, and not through this runner.
#
# *(An earlier draft of this comment described a `--print-url` flag on the verb.
# That flag was removed before merge — it was withheld-by-default rather than
# absent — and the paragraph outlived it by one round, telling the next editor to
# protect against a hazard that no longer existed while implying the capability
# was there to be re-enabled. Found in PR #59's round-2 review.)*
#
# WHY A VERB PARAMETER RATHER THAN A SECOND COPY OF THIS SCRIPT. Everything above
# — the commit verification, the hash-pinned lock, the no-build-isolation install,
# the refusals — is what makes it safe to execute anything at all on the host
# holding the Plaid master credential. A second runner for task 06a's probe would
# be a second copy of all of it, drifting from this one from its first commit. The
# verb is checked against an allow-list, so widening what may run is an edit to a
# named list rather than a consequence of the caller's argument.
#
# Usage, on the host, as the service user:
#
#   ./sandbox-rehearsal.sh <40-hex-commit> [--paths-only] [--verb <name>] \
#       [--flow <id>] [--link-mode <name>]
#
# --verb defaults to `rehearse-sandbox` (task 06). `probe-hosted-link` is task
# 06a's F7 criterion 2: mint a Hosted Link token and poll it before completion.
# `start-hosted-link` is task 06a's owner-run half: it mints a Sandbox session and
# emits it for the Mac-side driver, which is the widening described above. `complete-hosted-link`
# is the other end of that session and the only verb taking arguments of its own:
# `--flow` names the session `start-hosted-link` minted and `--link-mode` says
# which of 06a's three measurements to take. Neither has a default — the mode
# decides whether a `public_token` is spent, and a default that exchanges is a
# default that spends.
#
# `--paths-only` builds the environment and asks the verb which paths it selects,
# then stops. It makes no Plaid call, so it is the safe first run. It has no
# `complete-hosted-link` form: that verb selects the same two paths
# `start-hosted-link --paths-only` already prints, so the combination is refused
# rather than answered twice.

set -euo pipefail

# Overridable so the offline tests can point at a local fixture repository
# instead of the network. It is *printed* below rather than trusted silently: a
# run against a non-default origin says so in the transcript, which is the
# artefact that outlives the run.
readonly REPO_URL="${NETWORTH_REHEARSAL_ORIGIN:-https://github.com/orzzzl/networth}"
readonly CREDENTIAL="/etc/networth/plaid-sandbox.env"
readonly DEFAULT_VERB="rehearse-sandbox"
readonly ALLOWED_VERBS="rehearse-sandbox probe-hosted-link start-hosted-link complete-hosted-link"
# Named without their leading dashes so the allow-list holds plain words and the
# flag is *built* here from a word that matched one of them. A list of strings
# that already look like options is one careless expansion away from carrying an
# option nobody listed.
readonly ALLOWED_LINK_MODES="retrieve-only exchange exchange-twice"
readonly BUILD_REQUIREMENTS="requirements-build.txt"
readonly RUNTIME_REQUIREMENTS="requirements-runtime.txt"

die() {
	printf 'sandbox-rehearsal: %s\n' "$1" >&2
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
[ "${#commit}" -eq 40 ] || die "'$commit' is not a full commit id (40 hex characters); a short id is ambiguous and a ref that can move is not the thing that was reviewed"

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

# An allow-list, not a check for dangerous characters. This name is interpolated
# into the command the caller sends over ssh, and the list of spellings that mean
# something to a shell is not one anybody finishes writing — the SSH-option review
# rounds on this project cost four cycles proving exactly that. The set of verbs
# this runner may execute is four words long, so name them.
case " $ALLOWED_VERBS " in
*" $verb "*) ;;
*) die "'$verb' is not a verb this runner may execute; the allow-list is: $ALLOWED_VERBS" ;;
esac

# `complete-hosted-link` is the first verb that needs arguments of its own, and
# both of them are checked the way the commit is: against the grammar of the
# thing itself, never against a list of spellings a shell might find
# interesting. A `flow_id` is `uuid.uuid4().hex` (`tokenstore.new_flow_id`), so
# 32 lowercase hex characters is the whole of it.
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

# The couplings, refused here rather than left to argparse on the far side of an
# install. Each one is a caller who will otherwise watch a venv get built before
# being told the run was never going to work.
if [ "$verb" = "complete-hosted-link" ]; then
	[ "$mode" != "--paths-only" ] || die "complete-hosted-link has no --paths-only form; the paths it would print are the ones start-hosted-link --paths-only already prints for this environment"
	[ -n "$flow" ] || die "complete-hosted-link needs --flow <id>: the flow id start-hosted-link printed"
	[ -n "$link_mode" ] || die "complete-hosted-link needs --link-mode <name>; the allow-list is: $ALLOWED_LINK_MODES. There is no default: a default that exchanges is a default that spends"
elif [ -n "$flow" ] || [ -n "$link_mode" ]; then
	die "--flow and --link-mode mean something only to complete-hosted-link, and '$verb' would silently ignore them"
fi

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
command -v git >/dev/null || die "no git on this host; the reviewed source arrives as git objects so that their hashes are checked rather than trusted"

# Every requirement is `==`-pinned and carries at least one sha256. Reported per
# requirement rather than as a count, because "as many hashes as requirements" is
# satisfied by one requirement carrying two and another carrying none.
assert_fully_pinned() {
	local file="$1" offenders
	[ -r "$file" ] || die "$file is missing from the reviewed commit; the locked dependency set is what makes this an install of reviewed bytes rather than of whatever resolved today"
	offenders="$(
		awk '
			/^[[:space:]]*#/ { next }
			/^[[:space:]]*$/ { next }
			/^[^[:space:]]/ {
				if (seen && !hashed) { print name }
				name = $1; seen = 1; hashed = 0
				if (name !~ /==/) { print name " (not ==-pinned)" }
				next
			}
			/--hash=sha256:/ { hashed = 1 }
			END { if (seen && !hashed) { print name } }
		' "$file"
	)"
	[ -z "$offenders" ] || die "$file has requirements that are not pinned with a hash: $(printf '%s' "$offenders" | tr '\n' ' ')"
	grep -qE '^[^[:space:]#]' "$file" || die "$file lists no requirements at all; an empty lock cannot be the reviewed dependency set"
}

work="$(mktemp -d "${TMPDIR:-/tmp}/networth-rehearsal.XXXXXX")"
# On success or failure, and on an interrupt: the executable environment is the
# ephemeral part. Whatever the run wrote into the Sandbox database and token
# store is the result and stays.
trap 'rm -rf "$work"' EXIT INT TERM
src="$work/src"
venv="$work/venv"

printf 'commit        %s\n' "$commit"
printf 'origin        %s\n' "$REPO_URL"
printf 'verb          networth %s\n' "$verb"
printf 'identity      %s (uid %s), HOME=%s\n' "$(id -un)" "$(id -u)" "${HOME:-<unset>}"
printf 'workspace     %s (removed on exit)\n' "$work"

# Fetch exactly one commit. git verifies the object hashes on the way in, so the
# tree either is the reviewed one or the fetch fails — a stronger claim than
# asking a resolver afterwards what it thinks it installed.
mkdir -p "$src"
git init --quiet "$src"
git -C "$src" remote add origin "$REPO_URL"
git -C "$src" fetch --quiet --depth 1 origin "$commit" ||
	die "could not fetch $commit from $REPO_URL; a commit that is not pushed cannot be the one that was reviewed"
git -C "$src" checkout --quiet --detach FETCH_HEAD
head="$(git -C "$src" rev-parse HEAD)"
[ "$head" = "$commit" ] || die "the checkout is at '$head', not '$commit'"
printf 'source        verified at %s\n' "$head"

assert_fully_pinned "$src/$BUILD_REQUIREMENTS"
assert_fully_pinned "$src/$RUNTIME_REQUIREMENTS"

python3 -m venv "$venv"
# --no-cache-dir keeps the claim honest: a pip cache in the service user's home
# is a durable artefact of a run that promised to leave nothing behind.
pip_install() {
	"$venv/bin/pip" install --quiet --no-cache-dir --disable-pip-version-check \
		--require-hashes --no-deps "$@"
}
# The build backend first, and pinned, because `plaid-python` has no wheel: with
# build isolation pip would go and fetch a setuptools nobody reviewed in order to
# build it.
pip_install -r "$src/$BUILD_REQUIREMENTS"
pip_install --no-build-isolation -r "$src/$RUNTIME_REQUIREMENTS"
printf 'dependencies  installed from the reviewed lock, hash-verified\n\n'

# Deliberately not `exec`: `exec` replaces this process, and a replaced process
# runs no EXIT trap — the workspace would outlive the run that promised to remove
# it, which is the one property this whole script exists to provide.
#
# `-m networth` over the verified checkout, not an installed console script: this
# package is never built here, so no build backend of ours executes on the host
# and the bytes that run are the git objects checked above.
status=0
if [ "$mode" = "--paths-only" ]; then
	PYTHONPATH="$src" "$venv/bin/python" -m networth "$verb" --print-paths-only || status=$?
elif [ -n "$link_mode" ]; then
	# `--$link_mode` is built from a word that matched the allow-list above, so
	# the option that reaches the verb is one of exactly three spellings. These
	# are separate argv words rather than an interpolated string; nothing here
	# goes through a shell again.
	PYTHONPATH="$src" "$venv/bin/python" -m networth "$verb" --flow "$flow" "--$link_mode" || status=$?
else
	PYTHONPATH="$src" "$venv/bin/python" -m networth "$verb" || status=$?
fi
exit "$status"
