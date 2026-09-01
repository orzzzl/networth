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
# What it reads, and why that is the *blob* and not the file on disk: git
# publishes blobs, so blobs are what a check against publication must read. A
# symlink is the case that makes the difference visible — `[[ -f ]]` is false for
# one whose target does not exist, but git still stores and publishes the target
# text as the blob's content. Reading the working tree therefore skips exactly
# the entry an attacker (or an accident) can most easily leave behind. Tracked
# mode additionally scans the working copy of any file that differs from the
# index, so a developer's unstaged edit is not invisible to a local run.
#
# Not scanned: untracked files. They are not in the repository, and the
# pre-commit hook is the gate they must pass on the way in.
#
# Deliberately NOT provided: an allowlist, a "# nosecret" escape comment, or a
# skip-this-path flag. A scanner you can silence is one that gets silenced on
# the day it is right, and the only correct response to a finding here is to
# stop and remove the value — never to teach the check to ignore it.

set -euo pipefail

# Each entry is  NAME<TAB>REGEX. Kept as one list so adding a shape is a
# one-line change and the reporting stays uniform.
#
# The patterns match credential *shapes*, not the words that name them: this
# file, DESIGN.md and every task description discuss `access_token` and
# `client_id` constantly, and a scanner that fires on the prose is a scanner
# that gets switched off.
#
# They are matched against the whole file at once, not line by line, so
# `[[:space:]]` spans newlines. That is not a detail: `{"secret":\n "…"}` is
# valid JSON, and a line-oriented matcher never sees the key and the value
# together.
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
	sed -n '3,33p' "$0" >&2
	exit 2
}

die() {
	echo "check-no-secrets: $*" >&2
	exit 2
}

command -v perl >/dev/null 2>&1 || die "perl is required and was not found on PATH"

NETWORTH_SCAN_PATTERNS="$(patterns)"
export NETWORTH_SCAN_PATTERNS

# --- the matcher ---------------------------------------------------------------
#
# One pass per file, every pattern, whole-file semantics. Reports the location
# and the shape and never the matched text: a CI log is a public artifact too.
#
# Exit 0 = clean, 1 = matched, anything else = the matcher itself broke. That
# third case must not be silent — a malformed pattern that made the scan fail
# open would let the check go green forever.
scan_file() {
	local file="$1" label="$2" rc=0

	NETWORTH_SCAN_LABEL="$label" perl -0777 -e '
		my $label = $ENV{NETWORTH_SCAN_LABEL};
		my $data  = <STDIN>;
		$data = "" unless defined $data;
		my $found = 0;
		for my $entry (grep { length } split /\n/, $ENV{NETWORTH_SCAN_PATTERNS}) {
			my ($name, $re) = split /\t/, $entry, 2;
			die "pattern \"$name\" has no regex\n" unless defined $re && length $re;
			my $qr = eval { qr/$re/ } or die "pattern \"$name\" does not compile: $@";
			while ($data =~ /$qr/g) {
				my $start = $-[0];
				printf STDERR "SECRET-SHAPED [%s] %s:%d\n", $name, $label,
					1 + (substr($data, 0, $start) =~ tr/\n//);
				$found = 1;
				pos($data) = $start + 1 if $+[0] == $start;  # never loop on an empty match
			}
		}
		exit($found ? 1 : 0);
	' <"$file" || rc=$?

	case "$rc" in
	0) return 0 ;;
	1) return 1 ;;
	*) die "the matcher failed on '$label' (perl exited $rc)" ;;
	esac
}

# --- collect the files to scan -------------------------------------------------

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

# Repository-wide modes run from the top level. `git ls-files` is relative to the
# working directory, so a run from a subdirectory would scan a slice of the tree
# and report it clean.
if [[ $mode != explicit ]]; then
	git rev-parse --git-dir >/dev/null 2>&1 ||
		die "not a git repository, and no files were named"
	cd "$(git rev-parse --show-toplevel)"
fi

modified=()
shas=()
gitlinks=0

in_list() {
	local needle="$1" x
	shift
	for x in "$@"; do
		if [[ $x == "$needle" ]]; then return 0; fi
	done
	return 1
}

# Enumerate the index with its object ids rather than looking blobs up by path
# later. `git ls-files -s -z` emits "<mode> <sha> <stage>\t<path>\0", which gives
# the file mode too — and the mode is the difference between a blob and a
# gitlink, which has no content to read at all.
load_index() {
	local wanted=("$@") entry meta path filemode sha
	while IFS= read -r -d '' entry; do
		meta="${entry%%$'\t'*}"
		path="${entry#*$'\t'}"
		filemode="${meta%% *}"
		sha="${meta#* }"
		sha="${sha%% *}"

		if [[ $mode == staged ]] && ! in_list "$path" ${wanted[@]+"${wanted[@]}"}; then
			continue
		fi
		if [[ $filemode == 160000 ]]; then
			# A submodule: the index stores a commit id, not content. Skipping it
			# is not a hole — there is no blob here for a secret to live in — but
			# it is counted so a clean result never over-claims what it covered.
			gitlinks=$((gitlinks + 1))
			continue
		fi
		files+=("$path")
		shas+=("$sha")
	done < <(git ls-files -s -z)
}

case "$mode" in
tracked)
	load_index
	# Tracked files whose working copy differs from the index. Scanned in
	# addition to the blob so a local run still sees an unstaged edit.
	while IFS= read -r -d '' f; do modified+=("$f"); done < <(git diff --name-only -z)
	;;
staged)
	staged_paths=()
	while IFS= read -r -d '' f; do staged_paths+=("$f"); done \
		< <(git diff --cached --name-only --diff-filter=ACMR -z)
	load_index ${staged_paths[@]+"${staged_paths[@]}"}
	;;
esac

if [[ ${#files[@]} -eq 0 ]]; then
	if [[ $gitlinks -gt 0 ]]; then
		echo "check-no-secrets: nothing to scan ($gitlinks submodule(s) have no blob)"
	else
		echo "check-no-secrets: nothing to scan"
	fi
	exit 0
fi

# --- scan ----------------------------------------------------------------------

blob="$(mktemp)"
trap 'rm -f "$blob"' EXIT

found=0

# The blob git will publish for this path — read by object id, so the file mode
# on disk (regular, executable, symlink) cannot change what gets scanned.
scan_index_blob() {
	local path="$1" sha="$2"
	git cat-file blob "$sha" >"$blob" 2>/dev/null ||
		die "cannot read the index blob $sha for '$path'"
	scan_file "$blob" "$path" || found=1
}

# A path named on the command line, read the way git would store it: a symlink
# contributes its target text, which is the blob's content.
scan_named_path() {
	local path="$1"
	if [[ -L $path ]]; then
		printf '%s' "$(readlink -- "$path")" >"$blob"
	elif [[ -f $path ]]; then
		cat -- "$path" >"$blob"
	else
		die "'$path' is not a readable file"
	fi
	scan_file "$blob" "$path" || found=1
}

# `${a[@]+"${a[@]}"}`: bash 3.2 — the bash macOS ships — treats "${a[@]}" on an
# empty array as an unbound variable under `set -u`.
if [[ $mode == explicit ]]; then
	for f in ${files[@]+"${files[@]}"}; do
		scan_named_path "$f"
	done
else
	for i in "${!files[@]}"; do
		scan_index_blob "${files[i]}" "${shas[i]}"
	done
fi

for f in ${modified[@]+"${modified[@]}"}; do
	# The working copy may have been deleted; the blob above already covered it.
	if [[ -L $f ]]; then
		printf '%s' "$(readlink -- "$f")" >"$blob"
	elif [[ -f $f ]]; then
		cat -- "$f" >"$blob"
	else
		continue
	fi
	scan_file "$blob" "$f (working copy)" || found=1
done

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

if [[ ${#modified[@]} -gt 0 ]]; then
	echo "check-no-secrets: also scanned the working copy of ${#modified[@]} modified file(s)"
fi
if [[ $gitlinks -gt 0 ]]; then
	echo "check-no-secrets: skipped $gitlinks submodule(s), which store a commit id and no content"
fi
echo "check-no-secrets: ${#files[@]} file(s) scanned, clean"
