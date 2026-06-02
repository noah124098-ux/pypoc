#!/usr/bin/env bash
# logs.sh — tail logs from all running containers
set -euo pipefail

cd /opt/pypoc
docker compose logs -f --tail=50
