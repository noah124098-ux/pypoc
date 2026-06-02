#!/usr/bin/env bash
# stop.sh — gracefully stop all running services
set -euo pipefail

cd /opt/pypoc
docker compose down
