#!/usr/bin/env bash
# Everything CI runs, in the order CI runs it. If this passes, CI passes.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "==> ruff format --check"; uv run ruff format --check .
echo "==> ruff check";         uv run ruff check .
echo "==> mypy";               uv run mypy
echo "==> pytest";             uv run pytest
echo "==> check-no-secrets";   ./scripts/check-no-secrets.sh
