#!/usr/bin/env bash
#
# provision-host.sh — prepare the base host that runs the networth daemon.
#
# WHO RUNS THIS: the **owner**, as root, on the sync host. Agents never do —
# DESIGN.md §19 step 3 is "agents prepare everything, the owner runs it", and
# there is no rehearsal mode, because the first run is the one that changes
# sshd, the firewall and /etc/networth. An agent may read this host, write this
# script, and record what a run reported; it may not perform the run.
#
# It is one file with no dependencies beyond the base system, so the host never
# needs a checkout of this repository (§15: the host holds credentials, not
# code that can reach them by accident).
#
# WHAT IT DELIBERATELY REFUSES TO DO:
#
#   * It never modifies `PermitRootLogin`. This is not a fresh host: it is the
#     owner's Tailscale exit node, he administers it as root, and this project
#     does not get to lock him out of infrastructure that is not ours (§15.1,
#     §19 step 3.1). The change is *printed as a proposal*, with the ordering
#     that has to hold first; applying it is his decision on his own machine.
#   * It opens no port but SSH (§8.4 — v0 has no public inbound service), and
#     it never deletes, resets or disables a firewall rule. Rules that were
#     here before this project are the host's, not ours.
#   * It installs no application unit and no application virtualenv. Task 16
#     owns the units end to end; this script stops at the base host.
#   * It never reads, writes, moves or prints the contents of a credential.
#     It reports file *names*, owners and modes in /etc/networth — never a byte
#     of what is in them. Fixing an entry's mode does open a descriptor to it,
#     with `O_PATH`, which grants no read: `chown` and `chmod` need something to
#     address that is not a name (see `safe_path`), and that is all it is for.
#   * It asks for no password, ever (§15.1), and runs non-interactively.
#
# IDEMPOTENCE IS AN ACCEPTANCE CRITERION (task 28, criterion 4), so every step
# compares the current state before it acts and only then acts — a host that is
# already correct is not written to at all — and the run ends with a `changed:`
# count. A second run must print `changed: 0` AND leave the host byte-identical:
# `host-state.sh` is captured three times, once before run 1 and once after each
# run, and the last two captures must not differ. Two captures taken either side
# of both runs measure the two runs combined, which is expected to be non-empty
# and therefore cannot show that the second one changed nothing.

set -euo pipefail

readonly SERVICE_USER=networth
readonly SERVICE_HOME=/var/lib/networth
readonly SECRETS_DIR=/etc/networth
readonly DATA_DIR="$SERVICE_HOME/networth-data"
readonly AUTO_UPGRADES=/etc/apt/apt.conf.d/20auto-upgrades

# 20- rather than 50-: it must outrank cloud-init's `PasswordAuthentication yes`
# (`50-cloud-init.conf`, first match wins in sshd), and must NOT outrank a
# hardening drop-in the owner installed himself under `00-`. We beat the
# distribution default; we do not overrule him.
readonly SSHD_DROPIN=/etc/ssh/sshd_config.d/20-networth.conf

# The only sshd setting this script will ever write, kept as one constant so
# that what lands in the file is greppable from outside. `PermitRootLogin` is
# absent by design, and `tests/test_provision_script.py` pins its absence so a
# future edit that adds it fails CI rather than the owner's exit node.
readonly SSHD_DROPIN_BODY='PasswordAuthentication no'

readonly PYTHON_MIN_MAJOR=3
readonly PYTHON_MIN_MINOR=12

changed=0
warnings=0
apt_updated=0

step() { printf '\n== %s\n' "$*"; }
ok() { printf '   [ok]      %s\n' "$*"; }
note() { printf '   [note]    %s\n' "$*"; }
did() {
	changed=$((changed + 1))
	printf '   [changed] %s\n' "$*"
}
warn() {
	warnings=$((warnings + 1))
	printf '   [warn]    %s\n' "$*"
}
fail() {
	printf '\n[fail] %s\n' "$*" >&2
	exit 1
}

# apt is touched only when something is actually missing: a run that changes
# nothing must not refresh package lists either, or "changed: 0" would be a
# claim about this script rather than about the host.
apt_install() {
	if [[ $apt_updated -eq 0 ]]; then
		DEBIAN_FRONTEND=noninteractive apt-get update -qq
		apt_updated=1
	fi
	DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@" >/dev/null
}

package_installed() {
	dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "^install ok installed$"
}

# --- Root mutations the service account cannot aim -------------------------
#
# After the first run the *service account* owns $SERVICE_HOME and
# $SECRETS_DIR, so that unprivileged account controls the final component of
# every path below them. It can replace $DATA_DIR with a link to anywhere on
# the host and wait: the owner's next run would then chown and chmod the link's
# destination as root. Compromise of the daemon account must not become a root
# filesystem primitive on the next provisioning run.
#
# Checking the path first does not prevent that, because a check and a mutation
# are two separate pathname lookups and the account gets to act in between.
# `chown -h` is safe on its own, but `chmod` has no such option at all: GNU
# chmod dereferences a symbolic link given on the command line, by design
# (coreutils manual, "chmod invocation"). `install -d` follows one too. So a
# guard, however early, still leaves the mutation itself pointing at a name.
#
# Nothing here mutates a name. The path is opened ONCE, and every check, every
# mutation and the read-back afterwards address that descriptor:
#
#   * `O_PATH` opens no file. It grants no read, so a credential's bytes are
#     unreachable through the descriptor, and it does not block on a fifo.
#   * `O_NOFOLLOW` stops a final-component symlink from being resolved. With
#     `O_PATH` that yields a descriptor to the link ITSELF rather than an
#     error, so the type is checked on the descriptor, where nothing can swap
#     it, and a link is refused there.
#   * `/proc/self/fd/N` belongs to this process. `chown` and `chmod` through it
#     land on the object just inspected — there is no second lookup for the
#     service account to win.
#
# The comparison lives inside the same descriptor too, which is what keeps a
# correct host from being written to at all (criterion 4): "already right" is
# decided from the same `fstat` the mutation would act on, not from an earlier
# and separately racy one.
SAFE_PATH_PY=$(
	cat <<'PYTHON'
import grp
import os
import pwd
import stat
import sys

KINDS = {"dir": (stat.S_ISDIR, "a directory"), "file": (stat.S_ISREG, "a regular file")}


def die(message):
    sys.stderr.write("safe-path: %s\n" % message)
    raise SystemExit(1)


def described(info):
    try:
        user = pwd.getpwuid(info.st_uid).pw_name
    except KeyError:
        user = str(info.st_uid)
    try:
        group = grp.getgrgid(info.st_gid).gr_name
    except KeyError:
        group = str(info.st_gid)
    return "%s:%s %o" % (user, group, stat.S_IMODE(info.st_mode))


def main(argv):
    if len(argv) != 5:
        die("usage: ensure|ensuredir PATH dir|file USER:GROUP MODE")
    verb, path, kind, owner, mode_text = argv
    if verb not in ("ensure", "ensuredir"):
        die("unknown verb %r" % verb)
    if kind not in KINDS:
        die("unknown kind %r" % kind)
    if verb == "ensuredir" and kind != "dir":
        die("ensuredir creates directories only")
    if not hasattr(os, "O_PATH"):
        die("this Python has no os.O_PATH, so ownership cannot be changed without following links")
    mode = int(mode_text, 8)
    user, _, group = owner.partition(":")
    try:
        uid = pwd.getpwnam(user).pw_uid
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        die("no such user or group as %s" % owner)

    created = False
    try:
        fd = os.open(path, os.O_PATH | os.O_NOFOLLOW)
    except FileNotFoundError:
        if verb != "ensuredir":
            die("%s does not exist" % path)
        # `mkdir` never follows the final component: if anything is there by
        # now -- a directory, a file or a link planted since -- it fails with
        # EEXIST rather than acting on what that link points at.
        try:
            os.mkdir(path, mode)
        except FileExistsError:
            die("%s appeared while this script was creating it; nothing was changed" % path)
        created = True
        fd = os.open(path, os.O_PATH | os.O_NOFOLLOW)

    try:
        before = os.fstat(fd)
        if stat.S_ISLNK(before.st_mode):
            die(
                "%s is a symlink; refusing to change ownership or mode, which would "
                "act on whatever it points at" % path
            )
        recognises, description = KINDS[kind]
        if not recognises(before.st_mode):
            die("%s is not %s; this script does not replace what it finds" % (path, description))

        # Narrow before handing over, never the other way round: going
        # 755 -> 700 first means the moment in between is root-owned and
        # unreadable, where chowning first would briefly leave the service
        # account holding a directory the rest of the host can still read.
        pinned = "/proc/self/fd/%d" % fd
        if stat.S_IMODE(before.st_mode) != mode:
            os.chmod(pinned, mode)
        if (before.st_uid, before.st_gid) != (uid, gid):
            os.chown(pinned, uid, gid)

        # The postcondition, read back through the same descriptor: a link
        # substituted in the meantime cannot make this report a success.
        after = os.fstat(fd)
        if (after.st_uid, after.st_gid) != (uid, gid) or stat.S_IMODE(after.st_mode) != mode:
            die("%s is %s, not %s %o" % (path, described(after), owner, mode))

        if created:
            outcome = "created"
        elif described(after) != described(before):
            outcome = "changed"
        else:
            outcome = "unchanged"
        sys.stdout.write("%s|%s|%s\n" % (outcome, described(before), described(after)))
    finally:
        os.close(fd)


try:
    main(sys.argv[1:])
except OSError as error:
    # A traceback in the middle of the owner's provisioning transcript is a
    # worse artefact than one line naming the path and the reason, and the
    # `&&` chain stops on either.
    die("%s" % error)
PYTHON
)
readonly SAFE_PATH_PY

safe_path() { python3 -c "$SAFE_PATH_PY" "$@"; }

# `created|changed|unchanged|before|after` turned into the transcript's own
# vocabulary. Only the first two are root mutations, and only they count
# towards `changed:`.
report_path() {
	local path=$1 outcome before after
	IFS='|' read -r outcome before after <<<"$2"
	case $outcome in
	created) did "created $path ($after)" ;;
	changed) did "$path: $before -> $after" ;;
	unchanged) ok "$path is $after" ;;
	*) fail "safe_path reported '$outcome' for $path, which this script does not understand" ;;
	esac
}

# A clearer message than the descriptor-level refusal, for the case that is
# worth naming outright. This is a courtesy, not the defence: `safe_path`
# refuses a link whether or not this ran first.
require_not_symlink() {
	if [[ -L $1 ]]; then
		fail "$1 is a symlink, and this script changes ownership and mode as root — both act on a link's target, so it will not follow one. Replace it with a real directory, or remove it and run this again (it recreates what it owns)."
	fi
}

# ---------------------------------------------------------------------------

step "Host and script identity"

[[ $(id -u) -eq 0 ]] || fail "run this as root on the sync host (it changes sshd, the firewall and /etc/networth)"
command -v apt-get >/dev/null || fail "this script provisions a Debian-family host; apt-get is not on this one"

# Checked here rather than at "Python runtime" below, because `safe_path` — the
# only way this script changes an owner or a mode — is a `python3` program. The
# version floor is still that step's business; this is only about the
# interpreter existing before anything is written.
command -v python3 >/dev/null ||
	fail "python3 is not installed, and it is what makes this script's ownership changes refuse to follow a symlink; install python3 and run this again"

# The transcript has to name the machine it ran on and the exact script that
# ran: criteria (2) and (4) are read back from these two runs by someone who
# was not at the keyboard, and "a provisioning script" is not an identification.
if [[ -f ${BASH_SOURCE[0]} ]]; then
	note "script:   ${BASH_SOURCE[0]} (sha256 $(sha256sum "${BASH_SOURCE[0]}" | cut -d' ' -f1))"
else
	note "script:   read from stdin, so its checksum cannot be shown here; check the copy you piped in"
fi
note "host:     $(hostname) — $(. /etc/os-release && printf '%s' "$PRETTY_NAME"), kernel $(uname -r)"
note "run as:   $(id)"
note "utc now:  $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

step "Service user"

# A system account with no login shell and a locked password: the daemon owns
# the database and the secrets (§15.1), and nothing about that needs a way in.
if getent passwd "$SERVICE_USER" >/dev/null; then
	ok "user $SERVICE_USER exists"
	current_home=$(getent passwd "$SERVICE_USER" | cut -d: -f6)
	current_shell=$(getent passwd "$SERVICE_USER" | cut -d: -f7)
	[[ $current_home == "$SERVICE_HOME" ]] ||
		warn "home is $current_home, not $SERVICE_HOME — the database path follows the home directory; not changed by this script"
	case $current_shell in
	*/nologin | */false) ok "login shell is $current_shell" ;;
	*) warn "login shell is $current_shell, which permits an interactive login; not changed by this script" ;;
	esac
else
	useradd --system --create-home --home-dir "$SERVICE_HOME" --shell /usr/sbin/nologin "$SERVICE_USER"
	did "created system user $SERVICE_USER (home $SERVICE_HOME, shell /usr/sbin/nologin)"
fi

password_state=$(passwd -S "$SERVICE_USER" 2>/dev/null | awk '{print $2}' || true)
case $password_state in
L | NP) ok "$SERVICE_USER has no usable password ($password_state)" ;;
*) warn "$SERVICE_USER has a password set ($password_state); this script does not change passwords" ;;
esac

# The parent comes first in this list on purpose: $DATA_DIR is reached through
# $SERVICE_HOME, so rejecting a symlink at the parent has to happen before the
# child's path is resolved at all.
for directory in "$SERVICE_HOME" "$DATA_DIR"; do
	require_not_symlink "$directory"
	# Creating, comparing and correcting all happen against one descriptor:
	# nothing is written when nothing is wrong, so a rerun of a correct host
	# performs no root mutation here at all, and the decision is taken where
	# the service account cannot change the answer afterwards.
	result=$(safe_path ensuredir "$directory" dir "$SERVICE_USER:$SERVICE_USER" 700) ||
		fail "could not bring $directory to ${SERVICE_USER}:${SERVICE_USER} 700 — see the safe-path line above"
	report_path "$directory" "$result"
done

# `$DATA_DIR` is created here and stays empty: DESIGN.md §7 puts the database at
# `~/networth-data/networth.db` under this account and §15.1 makes this task's
# service user its owner, but nothing creates the parent — the migration runner
# opens the file, and SQLite does not create missing directories. Provisioning
# is where a directory the daemon needs at first start belongs; the database
# file itself is not ours to create.
note "$DATA_DIR is where the database will be created by the daemon; this script leaves it empty"

step "Secrets directory"

# The one step §15.1 requires to be loud: this directory holds the Plaid master
# credential, and a step that quietly adjusts permissions on it is
# indistinguishable from one that quietly widens them. Every entry is reported,
# changed or not, and nothing here reads a byte of any file.
require_not_symlink "$SECRETS_DIR"
result=$(safe_path ensuredir "$SECRETS_DIR" dir "$SERVICE_USER:$SERVICE_USER" 700) ||
	fail "could not bring $SECRETS_DIR to ${SERVICE_USER}:${SERVICE_USER} 700 — the credential files belong there; see the safe-path line above"
report_path "$SECRETS_DIR" "$result"

shopt -s nullglob dotglob
entries=("$SECRETS_DIR"/*)
shopt -u nullglob dotglob

if [[ ${#entries[@]} -eq 0 ]]; then
	note "$SECRETS_DIR is empty — the owner installs the credential files himself (AGENTS.md rule 3); no agent and no script writes them"
fi

for entry in ${entries[@]+"${entries[@]}"}; do
	if [[ -L $entry ]]; then
		# Not followed. `chown`/`chmod` through a symlink act on the target,
		# which can be any path on the host — a link in this directory is a way
		# to aim this step somewhere it was never meant to reach.
		warn "$entry is a symlink; not followed and not changed"
		continue
	fi
	if [[ -d $entry ]]; then
		kind=dir mode=700
	elif [[ -f $entry ]]; then
		kind=file mode=600
	else
		warn "$entry is neither a regular file nor a directory; left untouched"
		continue
	fi
	# `ensure`, never `ensuredir`: an entry that disappears between the test
	# above and the open must make this stop, not create something new in the
	# directory that holds the Plaid master credential. The kind is re-checked
	# on the descriptor, so a file swapped for a directory in that window is
	# refused rather than given a directory's mode.
	result=$(safe_path ensure "$entry" "$kind" "$SERVICE_USER:$SERVICE_USER" "$mode") ||
		fail "could not bring $entry to ${SERVICE_USER}:${SERVICE_USER} $mode — see the safe-path line above"
	report_path "$entry" "$result"
done

step "SSH: key-only login"

# `sshd -T` is the effective, merged configuration — the only honest source for
# "what is this host actually doing", since the answer is spread across the main
# file and every drop-in, first match winning.
command -v sshd >/dev/null || fail "sshd is not installed on this host; this script hardens an existing SSH server, it does not install one"
sshd_effective=$(sshd -T 2>/dev/null) || fail "sshd -T failed: this host's sshd configuration does not parse, and nothing here will touch it until that is fixed"
password_auth=$(awk '$1 == "passwordauthentication" {print $2}' <<<"$sshd_effective")
root_login=$(awk '$1 == "permitrootlogin" {print $2}' <<<"$sshd_effective")

if [[ $password_auth == no ]]; then
	ok "PasswordAuthentication is already no; nothing written"
else
	printf '%s\n' "$SSHD_DROPIN_BODY" >"$SSHD_DROPIN"
	chmod 644 "$SSHD_DROPIN"
	if ! sshd -t; then
		rm -f "$SSHD_DROPIN"
		fail "the drop-in made sshd's configuration invalid; it has been removed and sshd was not reloaded"
	fi
	systemctl reload ssh ||
		fail "wrote $SSHD_DROPIN, but 'systemctl reload ssh' failed, so the setting is not in effect yet"
	did "wrote $SSHD_DROPIN and reloaded ssh"
	password_auth=$(sshd -T 2>/dev/null | awk '$1 == "passwordauthentication" {print $2}')
	if [[ $password_auth != no ]]; then
		# Losing here means an earlier-sorting file sets it. Escalating (a name
		# that outranks everything) would silently overrule a decision someone
		# made on this host, so the script stops and names the candidates.
		printf '   [fail]    PasswordAuthentication is still %s. These files set it, and the first one wins:\n' "$password_auth"
		grep -rn -iE '^[[:space:]]*passwordauthentication' /etc/ssh/sshd_config.d/*.conf /etc/ssh/sshd_config 2>/dev/null |
			sed 's/^/             /'
		fail "refusing to outrank another drop-in automatically"
	fi
fi

# --- PermitRootLogin: reported and proposed, never applied. ---
note "PermitRootLogin is currently '$root_login' — read only; this script does not change it, ever"

sudo_members=$(getent group sudo | cut -d: -f4)
if [[ -n $sudo_members ]]; then
	for account in ${sudo_members//,/ }; do
		account_home=$(getent passwd "$account" | cut -d: -f6)
		keys=0
		if [[ -n $account_home && -f $account_home/.ssh/authorized_keys ]]; then
			keys=$(awk 'NF && $1 !~ /^#/ {n++} END {print n+0}' "$account_home/.ssh/authorized_keys")
		fi
		note "sudo account '$account' has $keys authorized key(s)"
	done
else
	note "no accounts are in the sudo group"
fi

# Only `yes` permits a password-based root login, which is the thing §15.1 is
# about. `prohibit-password` is already key-only, so proposing a change there
# would be proposing something that is already true — and a runbook that asks
# for work that is already done is how a real step gets skipped next time.
if [[ $root_login == prohibit-password || $root_login == forced-commands-only || $root_login == no ]]; then
	ok "root login over SSH is already key-only or narrower ('$root_login'); §15.1 needs nothing further, and any tightening beyond this is yours to decide"
else
	cat <<'PROPOSAL'

   ---- proposed, for you to apply or decline; this script has not done it ----

   Root login over SSH accepts a password on this host. Restricting it is the
   remaining hardening step in DESIGN.md §15.1, and it is the one that can
   strand you: this is your Tailscale exit node and you administer it as root.

   The ordering is the requirement, and every part of it is yours:

     1. A non-root account with sudo AND a working key of yours must exist.
        The counts printed above are that check — an account with 0 keys is
        not a way back in.
     2. Log in as that account from a SECOND, separate session and confirm
        sudo works there. Keep that session open.
     3. Only then, in the first session:

          echo 'PermitRootLogin prohibit-password' > /etc/ssh/sshd_config.d/10-root-login.conf
          sshd -t && systemctl reload ssh

        Verify from the second session before closing anything.

   Declining is a legitimate answer and is recorded as your decision on your
   own machine. Hardening that can strand you is not hardening.

   ---------------------------------------------------------------------------

PROPOSAL
fi

step "Firewall"

if ! command -v ufw >/dev/null; then
	apt_install ufw
	did "installed ufw"
fi

# `ufw status` lists no rules at all while the firewall is inactive, so asking
# it whether SSH is allowed answers "no" on every run of an inactive host and
# the rule gets re-added forever. `ufw show added` reports the configured rules
# in both states, which is the question being asked. The alternatives are
# matched too: `ufw allow ssh` and `ufw allow OpenSSH` open the same port, and
# treating them as absent would add a second, redundant rule on every run.
if ufw show added | grep -qE '^ufw allow (22|22/tcp|ssh|OpenSSH)$'; then
	ok "22/tcp is already allowed"
else
	ufw allow 22/tcp >/dev/null
	did "allowed 22/tcp (SSH — the only port this script opens, §8.4)"
fi

ufw_status=$(ufw status verbose)
if grep -q '^Status: active' <<<"$ufw_status"; then
	ok "ufw is active, default $(grep '^Default:' <<<"$ufw_status" | cut -d' ' -f2-)"
else
	# Enabling a firewall on a Tailscale exit node is the second operation in
	# this script that can take away something of the owner's that is not this
	# project's: `ufw enable` rebuilds netfilter, and tailscaled's ts-input and
	# ts-forward chains — which are what carry his VPN traffic and the exit
	# node's forwarding — are inserted rules, not persistent configuration. So
	# it gets the same treatment as PermitRootLogin: proposed, not applied.
	if command -v tailscale >/dev/null && [[ $(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo 0) == 1 ]]; then
		warn "ufw is inactive and this host forwards traffic (Tailscale exit node); NOT enabling it automatically"
		cat <<'PROPOSAL'

   ---- proposed, for you to apply or decline; this script has not done it ----

   `ufw enable` rebuilds netfilter from scratch. Tailscale's rules are inserted
   at the top of INPUT and FORWARD rather than stored in ufw, so enabling the
   firewall can drop exit-node forwarding and direct peer connections until
   tailscaled reinstalls them. The rule for SSH is already in place (above).

     ufw allow 41641/udp comment 'Tailscale direct connections'
     ufw enable
     tailscale status        # confirm the tailnet is still up, from another device

   ---------------------------------------------------------------------------

PROPOSAL
	else
		ufw --force enable >/dev/null
		did "enabled ufw (default deny incoming; 22/tcp open)"
	fi
fi

# Everything else that is open was opened by someone else. Reporting it is the
# §19 step 3.4 discipline — a new opening is an event, and an event needs a
# baseline to be visible against — and removing it is not this script's call.
printf '   [note]    firewall configuration now in place:\n'
{ ufw status verbose; ufw show added; } | sed '/^$/d;s/^/             /'

step "Unattended security upgrades"

if package_installed unattended-upgrades; then
	ok "unattended-upgrades is installed"
else
	apt_install unattended-upgrades
	did "installed unattended-upgrades"
fi

auto_upgrades_body='APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";'

# The effective value, merged across everything in /etc/apt/apt.conf.d, rather
# than the contents of one file — the same reason the SSH step reads `sshd -T`.
# A host that already enables these from another file is configured correctly,
# and rewriting a file to say what apt already reports would be churn that looks
# like work. (`0` and unset both mean disabled; anything else is a frequency in
# days and means enabled.)
periodic_lists=$(apt-config dump --format '%v%n' APT::Periodic::Update-Package-Lists 2>/dev/null || true)
periodic_upgrade=$(apt-config dump --format '%v%n' APT::Periodic::Unattended-Upgrade 2>/dev/null || true)

if [[ -n $periodic_lists && $periodic_lists != 0 && -n $periodic_upgrade && $periodic_upgrade != 0 ]]; then
	ok "apt already runs both periodic jobs (lists=$periodic_lists, upgrade=$periodic_upgrade)"
else
	printf '%s\n' "$auto_upgrades_body" >"$AUTO_UPGRADES"
	did "wrote $AUTO_UPGRADES (update package lists daily, apply security upgrades)"
fi

if [[ $(systemctl is-enabled unattended-upgrades 2>/dev/null || true) == enabled ]]; then
	ok "unattended-upgrades.service is enabled"
else
	systemctl enable --now unattended-upgrades >/dev/null 2>&1 ||
		fail "could not enable unattended-upgrades.service; security upgrades would not be applied and this host holds the Plaid credential"
	did "enabled unattended-upgrades.service"
fi

step "Python runtime"

command -v python3 >/dev/null || fail "python3 is not installed and this script does not choose an interpreter for you"
python_version=$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
python_major=${python_version%%.*}
python_rest=${python_version#*.}
python_minor=${python_rest%%.*}

if ((python_major > PYTHON_MIN_MAJOR || (python_major == PYTHON_MIN_MAJOR && python_minor >= PYTHON_MIN_MINOR))); then
	ok "python3 is $python_version (floor is ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}, from pyproject.toml)"
else
	fail "python3 is $python_version, below the ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR} floor this project requires"
fi

if package_installed python3-venv; then
	ok "python3-venv is installed"
else
	apt_install python3-venv
	did "installed python3-venv"
fi

# The package being present is a label; `python3 -m venv` succeeding is the
# fact. Debian splits `ensurepip` out of the interpreter, so a host can have
# python3 and still be unable to build an environment — which task 16 would
# discover while installing the units instead of here.
venv_probe=$(mktemp -d)
trap 'rm -rf "$venv_probe"' EXIT
if python3 -m venv "$venv_probe/probe" >/dev/null 2>&1 && [[ -x $venv_probe/probe/bin/python3 ]]; then
	ok "python3 -m venv works (probe built and discarded)"
else
	fail "python3 -m venv failed even though python3-venv is installed"
fi
rm -rf "$venv_probe"

note "no application virtualenv is created here — task 16 owns installing and running the daemon"

step "Public listener baseline"

# What is reachable from outside the tailnet, recorded so that a later check can
# ask whether the set *changed* rather than whether it is empty (§19 step 3.4).
# "This host has no public listener" is not ours to assert — sshd is supposed to
# be here, and it was here before this project.
tailnet_addresses=""
if command -v tailscale >/dev/null; then
	tailnet_addresses=$(tailscale ip 2>/dev/null || true)
fi

public_listeners=""
while read -r socket; do
	address=${socket%:*}
	# Quoted: an unquoted `[` opens a pattern bracket in this expansion and
	# swallows the rest of the file at parse time.
	address=${address#"["}
	address=${address%"]"}
	# `*` is NOT filtered out: it means every interface, which is the most
	# public a listener gets. Only loopback is unreachable from off the host.
	if [[ -z $address || $address == 127.* || $address == ::1 ]]; then
		continue
	fi
	if [[ -n $tailnet_addresses ]] && grep -qxF "$address" <<<"$tailnet_addresses"; then
		continue
	fi
	public_listeners+="$socket"$'\n'
	# A pipeline would run this loop in a subshell and the result would not
	# survive it; the redirection keeps it in this shell.
done < <(ss -ltnH | awk '{print $4}' | sort -u)
public_listeners=${public_listeners%$'\n'}

if [[ -z $public_listeners ]]; then
	note "no listener outside loopback and the tailnet"
else
	printf '   [note]    listeners reachable outside the tailnet (the baseline for §19 step 3.4):\n'
	printf '%s\n' "$public_listeners" | sed 's/^/             /'
	printf '   [note]    every entry above should be sshd; anything else is an opening this project did not make\n'
fi

if command -v tailscale >/dev/null; then
	funnel=$(tailscale funnel status 2>&1 || true)
	if grep -qi 'no serve config\|no funnel' <<<"$funnel"; then
		ok "no Tailscale Funnel is configured"
	else
		warn "tailscale funnel status did not report an empty configuration; read it yourself:"
		printf '%s\n' "$funnel" | sed 's/^/             /'
	fi
fi

step "Summary"

printf '   changed:  %d\n' "$changed"
printf '   warnings: %d\n' "$warnings"
cat <<'SUMMARY'

   Not done here, on purpose:
     * PermitRootLogin was not modified (task 28 criterion 1).
     * No port but 22/tcp was opened, and no firewall rule was removed.
     * No application unit, timer or virtualenv was installed — task 16.
     * No credential was read, written or printed. /etc/networth is the
       owner's to fill (DESIGN.md §15, AGENTS.md rule 3).

   Run this a second time: it must print `changed: 0`, and the host-state
   capture taken after that run must be identical to the one taken after this
   one. Three captures — before, between, after — are what make task 28's
   idempotence criterion falsifiable; two taken either side of both runs
   measure the runs combined and are expected to differ. DESIGN.md §19 step
   3.1 has the exact sequence.
SUMMARY
