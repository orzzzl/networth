#!/usr/bin/env bash
# First production install on the provisioned VPS. Never reads/prints secret values.
set -euo pipefail
commit="${1:-}"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo 'full commit required' >&2; exit 2; }
[[ "$(id -u)" = 0 ]] || { echo 'run as root on the provisioned VPS' >&2; exit 2; }
id networth >/dev/null
release="/opt/networth/releases/$commit"
if [[ -e /opt/networth/current && "$(readlink -f /opt/networth/current)" != "$release" ]]; then
  echo 'Existing runtime preserved; updates require a deliberate worker drain.' >&2
  exit 2
fi
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
runuser -u networth -- env HOME=/var/lib/networth NETWORTH_ENV=production /usr/local/bin/networth daemon init
install -m 0644 "$release/src"/systemd/networth-* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now networth-sync.service networth-link.service networth-serve.service \
  networth-sync.timer networth-publish.timer networth-archive.timer
systemctl start networth-publish.service networth-archive.service
cmp -s "$release/src/scripts/backup-ssh-dispatch" /usr/local/lib/networth/backup-ssh-dispatch
printf 'Installed runtime %s\n' "$commit"
systemctl is-active networth-link.service networth-serve.service networth-sync.timer networth-publish.timer networth-archive.timer
