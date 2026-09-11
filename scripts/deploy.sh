#!/usr/bin/env bash

set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.."
SOURCE_ENV="${SOURCE_ENV:-$REPO_ROOT/.env.production}"
PROD_COMPOSE="${PROD_COMPOSE:-$REPO_ROOT/docker-compose.prod.yml}"

if [[ ! -f "$SOURCE_ENV" ]]; then
  echo "Missing production environment at $SOURCE_ENV" >&2
  echo "Copy .env.production.example to .env.production and review it." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$SOURCE_ENV"
set +a

export APP_NAME="${APP_NAME:-Retirement Income}"
export REPO_ROOT
export SOURCE_ENV
export SERVER_FQDN="${SERVER_FQDN:-edgepi}"
export EDGE_NETWORK_ROOT="${EDGE_NETWORK_ROOT:-/mnt/ssd/edgepi/edge-network}"
export EDGE_DATA_ROOT="${EDGE_DATA_ROOT:-/mnt/ssd/edgepi/edge-data}"
export REMOTE_APP_DIR="${REMOTE_APP_DIR:-$EDGE_NETWORK_ROOT/income}"
export GHCR_IMAGE="${GHCR_IMAGE:-${RIE_IMAGE:-ghcr.io/timjjones-qodea/income-app:latest}}"
export LOCAL_IMAGE_NAME="${LOCAL_IMAGE_NAME:-retirement-income:latest}"
export PROJECT_DIR_SECRETS="${PROJECT_DIR_SECRETS:-/Users/timjones/Library/Mobile Documents/com~apple~CloudDocs/Personal/Apps/MTD_Bookkeeper/.data/secrets}"
export GHCR_SECRETS_DIR="${GHCR_SECRETS_DIR:-$PROJECT_DIR_SECRETS/github}"
export REMOTE_GHCR_SECRETS_DIR="${REMOTE_GHCR_SECRETS_DIR:-$EDGE_DATA_ROOT/shared/secrets/github}"
export BUILD_COMPOSE_FILE="${BUILD_COMPOSE_FILE:-$REPO_ROOT/docker-compose.yml}"
export SYNC_COMPOSE_FILE="${SYNC_COMPOSE_FILE:-$PROD_COMPOSE}"
export REMOTE_COMPOSE_FILE="${REMOTE_COMPOSE_FILE:-docker-compose.yaml}"
export IMAGE_ENV_VAR="${IMAGE_ENV_VAR:-RIE_IMAGE}"
export REMOTE_ENV_LINES="EDGE_DATA_ROOT=$EDGE_DATA_ROOT"
export LOCAL_GHCR_LOGIN_SCRIPT="${LOCAL_GHCR_LOGIN_SCRIPT:-/Users/timjones/Library/Mobile Documents/com~apple~CloudDocs/Personal/Apps/EDGE-network/scripts/ghcr-login.sh}"
export REMOTE_GHCR_LOGIN_SCRIPT="${REMOTE_GHCR_LOGIN_SCRIPT:-$EDGE_NETWORK_ROOT/scripts/ghcr-login.sh}"

SSH_OPTIONS=(
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=3
)

echo "Checking non-interactive EdgePi SSH access"
if ! ssh "${SSH_OPTIONS[@]}" "$SERVER_FQDN" true; then
  echo "Unable to connect non-interactively to '$SERVER_FQDN'." >&2
  echo "Check SERVER_FQDN and your SSH key/config before deploying." >&2
  exit 1
fi

echo "Validating production Compose"
docker compose --env-file "$SOURCE_ENV" -f "$PROD_COMPOSE" config --quiet

SHARED_DEPLOY_SCRIPT="${SHARED_DEPLOY_SCRIPT:-/Users/timjones/Library/Mobile Documents/com~apple~CloudDocs/Personal/Apps/EDGE-network/scripts/deploy-ghcr-app.sh}"

if [[ ! -x "$SHARED_DEPLOY_SCRIPT" ]]; then
  echo "Shared deploy script is missing or not executable: $SHARED_DEPLOY_SCRIPT" >&2
  exit 1
fi

"$SHARED_DEPLOY_SCRIPT"

echo "Application URL: https://${RIE_HOSTNAME:-inc.braeside-host.uk}"
