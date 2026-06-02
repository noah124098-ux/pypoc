#!/usr/bin/env bash
# start.sh — start the production stack
# PREREQ: backtest gate must be passing (Sharpe >= 1.2, file <= 30 days old)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

ENV_FILE="$REPO_ROOT/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "[error] .env not found at $ENV_FILE"
  echo "        Production requires a populated .env file. See .env.example."
  exit 1
fi

# Gate check — refuse to start prod if gate is not passing
echo "[prod] Checking backtest gate..."
if ! python "$REPO_ROOT/cli.py" check-gate --json 2>/dev/null | python -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('passed') else 1)"; then
  echo "[error] Backtest gate is not passing. Run walk-forward and fix strategies before deploying prod."
  echo "        python cli.py walk-forward --years 3"
  echo "        python cli.py check-gate"
  exit 1
fi

echo "[prod] Gate passed. Starting production stack..."
docker compose -f "$SCRIPT_DIR/docker-compose.prod.yml" up --build -d "$@"
echo "[prod] Stack started. View logs: docker compose -f $SCRIPT_DIR/docker-compose.prod.yml logs -f"
