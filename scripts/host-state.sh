#!/usr/bin/env bash
#
# host-state.sh — print everything `provision-host.sh` can change, and nothing
# else, in a form two runs can be diffed against each other.
#
# WHY IT EXISTS: task 28's idempotence criterion is "run the provisioning script
# twice and diff the host state". That sentence needs a definition of host state
# that is fixed in advance, or the diff becomes whatever the person looking
# happened to check. This is that definition, and it is deliberately the same
# list the provisioning script touches — a snapshot that omits what the script
# changes cannot fail.
#
# CAPTURE IT THREE TIMES, NOT TWICE. The criterion is that the SECOND run
# changes nothing, and only three captures can show that:
#
#   S0  before run 1   ─┐
#   S1  after  run 1   ─┴─ S0..S1 is provisioning's outcome: NON-EMPTY, and it
#                          must contain only the changes the reviewed script
#                          makes (the service user, the ownership under
#                          /etc/networth, the installed package).
#   S2  after  run 2   ──  S1..S2 MUST BE EMPTY. This is criterion (4).
#
# Capturing once before run 1 and once after run 2 measures the two runs
# combined. That diff is expected to be non-empty, so it can establish what
# provisioning did and cannot establish that re-running changed nothing —
# a different claim, and the one being accepted. Keep all three files.
#
# WHO RUNS THIS: anyone, including an agent, over SSH. It is **read-only**: it
# creates nothing, writes nothing, and starts, stops or reloads nothing. The two
# provisioning runs are the owner's alone (DESIGN.md §19 step 3, tasks/README.md
# task 28); because these captures change nothing, they can sit inside his
# sequence — and an agent can also take one at any time to check the record
# against the host.
#
# NO TIMESTAMPS, NO PIDS, NO HOSTNAME-OF-THE-MOMENT: output must differ between
# two runs only when the host differs. Anything that changes on its own would
# make every diff non-empty and the criterion unfalsifiable.
#
# It prints file names, owners and modes under /etc/networth — never contents.
# The credential files live there (DESIGN.md §15) and nothing may read them.

set -euo pipefail

readonly SERVICE_USER=networth
readonly SERVICE_HOME=/var/lib/networth
readonly SECRETS_DIR=/etc/networth

section() { printf '\n[%s]\n' "$*"; }

section "service user"
getent passwd "$SERVICE_USER" || echo "absent"
getent group "$SERVICE_USER" || echo "absent"

section "paths"
for path in "$SECRETS_DIR" "$SERVICE_HOME" "$SERVICE_HOME/networth-data"; do
	if [[ -e $path ]]; then
		stat -c '%n %U:%G %a %F' "$path"
	else
		echo "$path absent"
	fi
done
if [[ -d $SECRETS_DIR ]]; then
	# Names, owners and modes of what is in the credential directory. `-r` on
	# the sort keeps the order stable across runs; no content is read.
	find "$SECRETS_DIR" -mindepth 1 -maxdepth 1 -printf '%p %u:%g %m %y\n' 2>/dev/null | sort
fi

section "sshd effective"
sshd -T 2>/dev/null | grep -iE '^(passwordauthentication|permitrootlogin|pubkeyauthentication|kbdinteractiveauthentication|port|listenaddress) ' | sort

section "sshd config files setting auth"
grep -rn -iE '^[[:space:]]*(passwordauthentication|permitrootlogin)' \
	/etc/ssh/sshd_config /etc/ssh/sshd_config.d/ 2>/dev/null | sort || echo "none"

section "firewall"
ufw status verbose 2>/dev/null || echo "ufw absent"
ufw show added 2>/dev/null || true

section "listeners"
# PID and fd are stripped: they change on every restart and would make an
# unchanged host look changed.
ss -ltnpH 2>/dev/null | sed -E 's/pid=[0-9]+,fd=[0-9]+//g' | awk '{print $4, $NF}' | sort

section "packages"
for package in ufw unattended-upgrades python3-venv; do
	printf '%s %s\n' "$package" "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || echo 'not installed')"
done

section "apt periodic"
apt-config dump --format '%f %v%n' APT::Periodic::Update-Package-Lists APT::Periodic::Unattended-Upgrade 2>/dev/null || echo "unset"

section "units"
for unit in ssh unattended-upgrades; do
	printf '%s %s %s\n' "$unit" \
		"$(systemctl is-enabled "$unit" 2>/dev/null || echo unknown)" \
		"$(systemctl is-active "$unit" 2>/dev/null || echo unknown)"
done

section "python"
python3 --version 2>&1 || echo "python3 absent"
