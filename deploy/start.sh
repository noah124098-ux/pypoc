#!/usr/bin/env bash
# start.sh — pull latest images and start all services
set -euo pipefail

cd /opt/pypoc
docker compose pull
docker compose up -d --remove-orphans
docker compose ps
