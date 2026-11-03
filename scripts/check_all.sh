#!/usr/bin/env bash
# Run the full quality gate: formatting, lint, types, tests.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> black --check"
uv run black --check .

echo "==> ruff check"
uv run ruff check .

echo "==> mypy"
uv run mypy .

echo "==> pytest"
uv run pytest

echo "All checks passed."
