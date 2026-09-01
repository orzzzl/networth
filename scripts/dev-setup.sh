#!/usr/bin/env bash
#
# One command: toolchain, virtualenv, git hooks. Safe to re-run.
#
# The only prerequisite is uv (https://docs.astral.sh/uv/), which also installs
# the Python this project targets — so a clean machine needs exactly one tool
# rather than a Python of the right version plus a package manager.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if ! command -v uv >/dev/null 2>&1; then
	cat >&2 <<'MSG'
dev-setup: uv is not installed.

  curl -LsSf https://astral.sh/uv/install.sh | sh

Then re-run this script. uv installs Python 3.12 itself; you do not need to
install Python separately.
MSG
	exit 1
fi

echo "==> syncing the environment (installs Python 3.12 if missing)"
uv sync

echo "==> installing git hooks"
git config core.hooksPath .githooks
chmod +x .githooks/* scripts/*.sh

echo "==> verifying the secret scanner runs"
./scripts/check-no-secrets.sh >/dev/null

cat <<'MSG'

Ready. From here:

  uv run pytest              tests
  uv run ruff format         format
  uv run ruff check --fix    lint
  uv run mypy                types
  uv run networth demo       the CLI
  ./scripts/check.sh         everything CI runs, in one command
MSG
