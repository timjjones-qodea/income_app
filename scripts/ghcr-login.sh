#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../MTD_Bookkeeper" && pwd)"
GHCR_SECRET_DIR="${GHCR_SECRET_DIR:-$REPO_ROOT/.data/secrets/github}"
GHCR_USER_FILE="${GHCR_USER_FILE:-$GHCR_SECRET_DIR/ghcr_username}"
GHCR_TOKEN_FILE="${GHCR_TOKEN_FILE:-$GHCR_SECRET_DIR/ghcr_token}"

if [[ ! -f "$GHCR_USER_FILE" || ! -f "$GHCR_TOKEN_FILE" ]]; then
  echo "Missing GHCR credentials under $GHCR_SECRET_DIR" >&2
  exit 1
fi

GHCR_USER="$(<"$GHCR_USER_FILE")"
GHCR_TOKEN="$(<"$GHCR_TOKEN_FILE")"

printf '%s' "$GHCR_TOKEN" | docker login ghcr.io \
  --username "$GHCR_USER" \
  --password-stdin

echo "Logged in to GHCR as $GHCR_USER"
