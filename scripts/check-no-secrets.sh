#!/usr/bin/env bash
#
# check-no-secrets.sh — refuse to let credential-shaped text into a PUBLIC repo.
#
# This repository is world-readable and anything committed once stays in the
# history even after it is deleted (AGENTS.md rule 0, DESIGN.md §15). The
# credentials in reach of this project are long-lived and grant read access to
# real financial accounts, so the cheap check runs everywhere: as a pre-commit
# hook and as a CI job on every PR.
#
# Usage:
#   check-no-secrets.sh                 scan every tracked file (CI)
#   check-no-secrets.sh --staged        scan what is staged (pre-commit hook)
#   check-no-secrets.sh FILE [FILE...]  scan the named files (tests)
#
# Exit 0 = clean. Exit 1 = something matched. Exit 2 = the scanner itself failed.
#
# Deliberately NOT provided: an allowlist, a "# nosecret" escape comment, or a
# skip-this-path flag. A scanner you can silence is one that gets silenced on
# the day it is right, and the only correct response to a finding here is to
# stop and remove the value — never to teach the check to ignore it.

set -euo pipefail

# Each entry is  NAME<TAB>EXTENDED_REGEX. Kept as one list so adding a shape is
# a one-line change and the reporting stays uniform.
#
# The patterns match credential *shapes*, not the words that name them: this
# file, DESIGN.md and every task description discuss `access_token` and
# `client_id` constantly, and a scanner that fires on the prose is a scanner
# that gets switched off.
patterns() {
	local hex8='[0-9a-f]{8}'
	local hex4='[0-9a-f]{4}'
	local hex12='[0-9a-f]{12}'
	local uuid="${hex8}-${hex4}-${hex4}-${hex4}-${hex12}"
	local env='(sandbox|development|production)'

	# Split across two adjacent literals on purpose. Every other pattern here is
	# a regex, and a regex does not match its own source text — but this one is
	# the literal base64 prefix of an OpenSSH private key, so written whole it
	# would match this file and the scanner would fail on itself. The fix is to
	# break the literal, never to teach the scanner to skip a path: the moment
	# there is a path it does not look at, that is where a secret ends up.
	local openssh_key_b64='b3BlbnNz''aC1rZXktdjEA'

	printf '%s\t%s\n' \
		'plaid-access-token' "access-${env}-${uuid}" \
		'plaid-public-or-link-token' "(public|link)-${env}-${uuid}" \
		'plaid-client-id-or-secret' \
		"(client_id|CLIENT_ID|secret|SECRET)[\"']?[[:space:]]*[:=][[:space:]]*[\"']?[0-9a-f]{24,}" \
		'access-token-json-value' \
		"[\"']access_token[\"'][[:space:]]*:[[:space:]]*[\"'][^\"']{8,}[\"']" \
		'private-key-header' '-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----' \
		'age-secret-key' 'AGE-SECRET-KEY-1[0-9A-Z]{20,}' \
		'ssh-private-key-body' "$openssh_key_b64"
}

usage() {
	sed -n '3,20p' "$0" >&2
	exit 2
}

# --- collect the files to scan -----------------------------------------------

mode=tracked
files=()

if [[ ${1-} == "--help" || ${1-} == "-h" ]]; then
	usage
elif [[ ${1-} == "--staged" ]]; then
	mode=staged
	shift
	[[ $# -eq 0 ]] || usage
elif [[ $# -gt 0 ]]; then
	mode=explicit
	files=("$@")
fi

case "$mode" in
tracked)
	if ! git rev-parse --git-dir >/dev/null 2>&1; then
		echo "check-no-secrets: not a git repository, and no files were named" >&2
		exit 2
	fi
	while IFS= read -r -d '' f; do files+=("$f"); done < <(git ls-files -z)
	;;
staged)
	while IFS= read -r -d '' f; do files+=("$f"); done \
		< <(git diff --cached --name-only --diff-filter=ACMR -z)
	;;
esac

if [[ ${#files[@]} -eq 0 ]]; then
	echo "check-no-secrets: nothing to scan"
	exit 0
fi

# --- scan ---------------------------------------------------------------------
#
# In --staged mode the *staged* content is what matters, not what is on disk:
# `git add` a secret, edit it out of the working tree, and the commit still
# carries it. So staged blobs are read out of the index.

read_file() {
	local path="$1"
	if [[ $mode == staged ]]; then
		git show ":$path" 2>/dev/null || true
	else
		[[ -f $path ]] && cat -- "$path" 2>/dev/null || true
	fi
}

found=0

while IFS=$'\t' read -r name regex; do
	[[ -n ${name:-} ]] || continue
	for f in "${files[@]}"; do
		content="$(read_file "$f")"
		[[ -n $content ]] || continue
		# -a: treat as text so a binary-looking blob cannot hide a match.
		if hits="$(printf '%s' "$content" | grep -aInE -- "$regex" || true)"; [[ -n $hits ]]; then
			found=1
			while IFS= read -r hit; do
				# Report the line number and the shape, never the value: a CI log
				# is a public artifact too.
				echo "SECRET-SHAPED [$name] $f:${hit%%:*}" >&2
			done <<<"$hits"
		fi
	done
done < <(patterns)

if [[ $found -ne 0 ]]; then
	cat >&2 <<'MSG'

check-no-secrets: refusing to proceed.

This repository is PUBLIC and its history is permanent. Remove the value — do
not comment it out, do not move it to another file in this repo, and do not add
an exception to this scanner.

Where credentials actually belong: /etc/networth/ on the sync host, and
~/agents/secrets/ on zelengs-macbook-air-2 (DESIGN.md §15). If a test needs a
credential-shaped string, generate it at run time; do not commit a fixture.
MSG
	exit 1
fi

echo "check-no-secrets: ${#files[@]} file(s) scanned, clean"
