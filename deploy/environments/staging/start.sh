#!/usr/bin/env bash
# start.sh — start the staging stack
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

ENV_FILE="$REPO_ROOT/.env.staging"
if [ ! -f "$ENV_FILE" ]; then
  echo "[warn] $ENV_FILE not found — copy .env.staging.example and fill in credentials"
  echo "       cp $REPO_ROOT/.env.staging.example $ENV_FILE"
  exit 1
fi

echo "[staging] Starting staging stack (dashboard :8501, mcp :8011)..."
docker compose -f "$SCRIPT_DIR/docker-compose.staging.yml" up --build -d "$@"
echo "[staging] Stack started. View logs: docker compose -f $SCRIPT_DIR/docker-compose.staging.yml logs -f"
