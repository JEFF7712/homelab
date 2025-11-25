#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$HOME/homelab"

echo "[deploy] Changing to repo directory..."
cd "$REPO_DIR"

echo "[deploy] Pulling latest changes..."
git pull --ff-only

echo "[deploy] Deploying monitoring stack..."
cd "$REPO_DIR/docker/monitoring"
docker compose pull
docker compose up -d

echo "[deploy] Deploying logging stack..."
cd "$REPO_DIR/docker/logging"
docker compose pull
docker compose up -d

echo "[deploy] Deploying glance..."
cd "$REPO_DIR/docker/glance"
docker compose pull
docker compose up -d

echo "[deploy] Deploying n8n..."
cd "$REPO_DIR/docker/n8n"
docker compose up -d --build

echo "[deploy] Deploying pihole..."
cd "$REPO_DIR/docker/pihole"
docker compose pull
docker compose up -d

echo "[deploy] Deploying homeassistant..."
cd "$REPO_DIR/docker/homeassistant"
docker compose pull
docker compose up -d

echo "[deploy] Building APIs..."
cd "$REPO_DIR/docker/apis"
docker compose build --no-cache

echo "[deploy] Deploying APIs..."
docker compose up -d

echo "[deploy] Deploying Cloudflare Tunnel..."
cd "$REPO_DIR/docker/cloudflared"
docker compose pull
docker compose up -d

echo "[deploy] Cleaning up old images..."
docker image prune -f

echo "[deploy] Done."
