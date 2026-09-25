#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash -n "$ROOT/scripts/dev.sh" "$ROOT/scripts/check.sh"
cd "$ROOT/backend"
uv run --locked ruff check . ../scripts
uv run --locked pytest -q
cd "$ROOT/web"
npm run test
npm run typecheck
npm run lint
npm run build
