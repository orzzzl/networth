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
#     of what is in them.
#   * It asks for no password, ever (§15.1), and runs non-interactively.
#
# IDEMPOTENCE IS AN ACCEPTANCE CRITERION (task 28, criterion 4), so every step
# compares the current state before it acts and the run ends with a `changed:`
# count. A second run must print `changed: 0`: that count, and a diff of the two
# transcripts, is what makes idempotence checkable rather than asserted.

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

# owner:group and mode of a path, as one comparable string.
state_of() { stat -c '%U:%G %a' "$1"; }

# ---------------------------------------------------------------------------

step "Host and script identity"

[[ $(id -u) -eq 0 ]] || fail "run this as root on the sync host (it changes sshd, the firewall and /etc/networth)"
command -v apt-get >/dev/null || fail "this script provisions a Debian-family host; apt-get is not on this one"

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

for directory in "$SERVICE_HOME" "$DATA_DIR"; do
	if [[ ! -d $directory ]]; then
		install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 700 "$directory"
		did "created $directory (${SERVICE_USER}:${SERVICE_USER}, mode 700)"
		continue
	fi
	before=$(state_of "$directory")
	chown "$SERVICE_USER:$SERVICE_USER" "$directory"
	chmod 700 "$directory"
	after=$(state_of "$directory")
	if [[ $before == "$after" ]]; then
		ok "$directory is $after"
	else
		did "$directory: $before -> $after"
	fi
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
if [[ ! -d $SECRETS_DIR ]]; then
	install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 700 "$SECRETS_DIR"
	did "created $SECRETS_DIR (${SERVICE_USER}:${SERVICE_USER}, mode 700)"
else
	before=$(state_of "$SECRETS_DIR")
	chown "$SERVICE_USER:$SERVICE_USER" "$SECRETS_DIR"
	chmod 700 "$SECRETS_DIR"
	after=$(state_of "$SECRETS_DIR")
	if [[ $before == "$after" ]]; then
		ok "$SECRETS_DIR is $after"
	else
		did "$SECRETS_DIR: $before -> $after"
	fi
fi

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
		mode=700
	elif [[ -f $entry ]]; then
		mode=600
	else
		warn "$entry is neither a regular file nor a directory; left untouched"
		continue
	fi
	before=$(state_of "$entry")
	chown "$SERVICE_USER:$SERVICE_USER" "$entry"
	chmod "$mode" "$entry"
	after=$(state_of "$entry")
	if [[ $before == "$after" ]]; then
		ok "$entry is $after"
	else
		did "$entry: $before -> $after"
	fi
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

   Run this a second time: it must print `changed: 0`. That, plus a diff of
   the two transcripts, is task 28's idempotence criterion.
SUMMARY
