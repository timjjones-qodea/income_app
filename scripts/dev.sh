#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

export RIE_DATA_DIR="${RIE_DATA_DIR:-$REPO_ROOT/.data/dev}"
mkdir -p "$RIE_DATA_DIR"

PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="${PYTHON_FALLBACK:-python3}"
fi

exec "$PYTHON" -m uvicorn app.main:app \
  --reload \
  --reload-dir app \
  --host 127.0.0.1 \
  --port "${RIE_PORT:-8010}"
