#!/usr/bin/env bash
# start.sh — start the dev stack
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

ENV_FILE="$REPO_ROOT/.env.dev"
if [ ! -f "$ENV_FILE" ]; then
  echo "[warn] $ENV_FILE not found — copy .env.dev.example and fill in credentials"
  echo "       cp $REPO_ROOT/.env.dev.example $ENV_FILE"
  exit 1
fi

echo "[dev] Starting dev stack (dashboard :8502, mcp :8010)..."
docker compose -f "$SCRIPT_DIR/docker-compose.dev.yml" up --build "$@"
