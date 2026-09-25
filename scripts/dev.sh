#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$ROOT/backend/.venv/bin/python" ]]; then
  echo 'Backend dependencies missing. Run: cd backend && uv sync --locked' >&2
  exit 1
fi
exec "$ROOT/backend/.venv/bin/python" "$ROOT/scripts/dev.py"
