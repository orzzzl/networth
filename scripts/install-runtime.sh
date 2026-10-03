#!/usr/bin/env bash
# Install/upgrade on the provisioned VPS; never read or print credential values.
set -euo pipefail
commit="${1:-}"
mode="${2:-}"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo 'full commit required' >&2; exit 2; }
[[ -z "$mode" || "$mode" = --upgrade-if-idle ]] || { echo 'unknown install mode' >&2; exit 2; }
[[ "$(id -u)" = 0 ]] || { echo 'run as root on the provisioned VPS' >&2; exit 2; }
id networth >/dev/null
# Task08's mint command must take this same lock through request persistence.
exec 9>/var/lib/networth/.runtime-deploy.lock
chown networth:networth /var/lib/networth/.runtime-deploy.lock
chmod 0600 /var/lib/networth/.runtime-deploy.lock
flock -n 9 || { echo 'Another deployment or mint is active; retry later.' >&2; exit 2; }
release="/opt/networth/releases/$commit"
upgrading=false
if [[ -e /opt/networth/current ]]; then
  [[ "$mode" = --upgrade-if-idle ]] || {
    echo 'Existing runtime preserved; use --upgrade-if-idle to drain it safely.' >&2; exit 2;
  }
  upgrading=true
fi
assert_no_pending_link() {
  runuser -u networth -- env HOME=/var/lib/networth NETWORTH_ENV=production \
    PYTHONPATH=/opt/networth/current/src /opt/networth/current/venv/bin/python - <<'PY'
import sqlite3
from networth.plaid.environment import paths_for, selected_environment
path = paths_for(selected_environment()).database
with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
    count = db.execute("SELECT count(*) FROM link_request WHERE material_reaped_at IS NULL OR EXISTS (SELECT 1 FROM link_result r WHERE r.flow_id = link_request.flow_id AND r.state = 'EXCHANGING')").fetchone()[0]
if count:
    raise SystemExit('Pending Link evidence exists; runtime upgrade refused.')
print('Upgrade preflight: no pending Link requests.')
PY
}
if $upgrading; then assert_no_pending_link; fi
install -d -m 0755 /opt/networth/releases
if [[ ! -d "$release/src/.git" ]]; then
  install -d -m 0755 "$release"
  git clone --quiet https://github.com/orzzzl/networth "$release/src"
  git -C "$release/src" checkout --quiet --detach "$commit"
fi
[[ "$(git -C "$release/src" rev-parse HEAD)" = "$commit" ]]
[[ -z "$(git -C "$release/src" status --porcelain)" ]]
python3 -m venv "$release/venv"
"$release/venv/bin/pip" install --quiet --disable-pip-version-check --no-cache-dir \
  --require-hashes --no-deps -r "$release/src/requirements-build.txt"
"$release/venv/bin/pip" install --quiet --disable-pip-version-check --no-cache-dir \
  --require-hashes --no-deps --no-build-isolation -r "$release/src/requirements-runtime.txt"
config=$(mktemp)
trap 'rm -f "$config"' EXIT
cat > "$config" <<'EOF'
NETWORTH_ENV=production
NETWORTH_ARCHIVE_DIR=/var/lib/networth/archives
EOF
if [[ -e /etc/networth/networth.env ]]; then
  cmp -s "$config" /etc/networth/networth.env || {
    echo 'Existing runtime configuration preserved; differs from install configuration.' >&2; exit 2;
  }
else
  install -o networth -g networth -m 0600 "$config" /etc/networth/networth.env
fi
if $upgrading; then
  systemctl stop networth-sync.timer networth-publish.timer networth-archive.timer
  # Link has no forced-stop deadline: returned credentials must reach durability.
  systemctl stop networth-link.service networth-sync.service networth-publish.service networth-archive.service networth-serve.service
  if ! assert_no_pending_link; then
    systemctl start networth-link.service networth-serve.service networth-sync.timer networth-publish.timer networth-archive.timer
    exit 2
  fi
fi
ln -sfn "$release" /opt/networth/current
for executable in networth networth-serve; do
  module=networth.cli
  [[ "$executable" = networth-serve ]] && module=networth.serve
  cat > "/usr/local/bin/$executable" <<EOF
#!/opt/networth/current/venv/bin/python
import sys
sys.path.insert(0, '/opt/networth/current/src')
from $module import main
raise SystemExit(main())
EOF
  chmod 0755 "/usr/local/bin/$executable"
done
install -d -m 0755 /usr/local/lib/networth
install -m 0755 "$release/src/scripts/backup-ssh-dispatch" /usr/local/lib/networth/backup-ssh-dispatch
runuser -u networth -- env HOME=/var/lib/networth NETWORTH_ENV=production /usr/local/bin/networth daemon init
install -m 0644 "$release/src"/systemd/networth-* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now networth-sync.service networth-link.service networth-serve.service \
  networth-sync.timer networth-publish.timer networth-archive.timer
systemctl start networth-publish.service networth-archive.service
cmp -s "$release/src/scripts/backup-ssh-dispatch" /usr/local/lib/networth/backup-ssh-dispatch
printf 'Installed runtime %s\n' "$commit"
systemctl is-active networth-link.service networth-serve.service networth-sync.timer networth-publish.timer networth-archive.timer
