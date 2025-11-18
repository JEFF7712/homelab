#!/usr/bin/env bash
set -e

REPO_DIR="$HOME/homelab"

cd "$REPO_DIR"
echo "[deploy] Pulling latest changes..."
git pull --ff-only

echo "[deploy] Deploying main stack..."
cd "$REPO_DIR/docker"
docker compose pull
docker compose up -d

#echo "[deploy] Deploying automations stack..."
#cd "$REPO_DIR/docker/automations"
#docker compose pull
#docker compose up -d
#
#echo "[deploy] Deploying glance..."
#cd "$REPO_DIR/docker/glance"
#docker compose pull
#docker compose up -d

echo "[deploy] Done."
