#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

SERVER_FQDN="${SERVER_FQDN:-edgepi}"
EDGE_NETWORK_ROOT="${EDGE_NETWORK_ROOT:-/mnt/ssd/edgepi/edge-network}"
EDGE_DATA_ROOT="${EDGE_DATA_ROOT:-/mnt/ssd/edgepi/edge-data}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-$EDGE_NETWORK_ROOT/income}"
GHCR_IMAGE="${GHCR_IMAGE:-${RIE_IMAGE:-ghcr.io/timjjones-qodea/income-app:latest}}"
LOCAL_IMAGE_NAME="${LOCAL_IMAGE_NAME:-retirement-income:latest}"
GHCR_LOGIN_SCRIPT="${GHCR_LOGIN_SCRIPT:-$EDGE_NETWORK_ROOT/scripts/ghcr_login.sh}"
SSH_OPTIONS=(
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=3
)
RSYNC_SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3"

echo "Starting Retirement Income deployment to ${SERVER_FQDN}"
cd "$REPO_ROOT"

echo "Checking non-interactive EdgePi SSH access"
if ! ssh "${SSH_OPTIONS[@]}" "$SERVER_FQDN" true; then
  echo "Unable to connect non-interactively to '$SERVER_FQDN'." >&2
  echo "Check SERVER_FQDN and your SSH key/config before deploying." >&2
  exit 1
fi

echo "Validating production Compose"
docker compose --env-file "$SOURCE_ENV" -f "$PROD_COMPOSE" config --quiet

echo "Pulling latest source"
git pull --ff-only

echo "Building application image"
docker compose -f docker-compose.yml build

if ! docker image inspect "$LOCAL_IMAGE_NAME" >/dev/null 2>&1; then
  echo "Unable to find local image '$LOCAL_IMAGE_NAME' after build" >&2
  exit 1
fi

echo "Tagging and pushing ${GHCR_IMAGE}"
docker tag "$LOCAL_IMAGE_NAME" "$GHCR_IMAGE"
docker push "$GHCR_IMAGE"

echo "Preparing EdgePi directories"
ssh "${SSH_OPTIONS[@]}" "$SERVER_FQDN" "mkdir -p '$REMOTE_APP_DIR' '$EDGE_DATA_ROOT/income/data'"

echo "Syncing production Compose and environment"
rsync -e "$RSYNC_SSH" -a "$PROD_COMPOSE" "${SERVER_FQDN}:${REMOTE_APP_DIR}/docker-compose.yaml"
rsync -e "$RSYNC_SSH" -a "$SOURCE_ENV" "${SERVER_FQDN}:${REMOTE_APP_DIR}/.env"

echo "Deploying remotely on ${SERVER_FQDN}"
ssh "${SSH_OPTIONS[@]}" "$SERVER_FQDN" bash <<EOF
set -Eeuo pipefail

cd "$EDGE_NETWORK_ROOT"
./scripts/maintain-env.sh

cd "$REMOTE_APP_DIR"
if [[ -x "$GHCR_LOGIN_SCRIPT" ]]; then
  "$GHCR_LOGIN_SCRIPT"
fi

docker compose pull
docker compose up -d --remove-orphans
docker image prune -f
docker compose ps
EOF

echo
echo "Deployment complete: ${GHCR_IMAGE}"
echo "Application URL: https://${RIE_HOSTNAME:-inc.braeside-host.uk}"
