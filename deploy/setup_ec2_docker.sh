#!/usr/bin/env bash
# setup_ec2_docker.sh — one-shot Docker setup for a fresh EC2 Ubuntu instance
# Run as: sudo bash setup_ec2_docker.sh

set -euo pipefail

REPO_URL="https://github.com/noah124098-ux/pypoc.git"
INSTALL_DIR="/opt/pypoc"

echo "==> Updating package index..."
apt-get update -y

echo "==> Installing Docker via get.docker.com..."
curl -fsSL https://get.docker.com | sh

echo "==> Installing docker-compose-plugin..."
apt-get install -y docker-compose-plugin

echo "==> Enabling Docker to start on boot..."
systemctl enable docker
systemctl start docker

echo "==> Cloning repo to ${INSTALL_DIR}..."
if [ -d "${INSTALL_DIR}/.git" ]; then
    echo "    Repo already present — pulling latest."
    git -C "${INSTALL_DIR}" pull
else
    git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

echo ""
echo "======================================================"
echo "Setup complete."
echo "Fill in .env then run:"
echo "  cd ${INSTALL_DIR} && docker compose up -d"
echo "======================================================"
