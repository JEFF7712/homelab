#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$HOME/homelab"

if [[ -f "$REPO_DIR/.env" ]]; then
  echo "[deploy] Loading environment variables from .env..."
  set -a
  source "$REPO_DIR/.env"
  set +a
else
  echo "[deploy] ERROR: .env file not found at $REPO_DIR/.env" >&2
  exit 1
fi

echo "[deploy] Changing to repo directory..."
cd "$REPO_DIR"

echo "[deploy] Pulling latest changes..."
git pull --ff-only

echo "[deploy] Deploying monitoring stack..."
cd "$REPO_DIR/docker/monitoring"
docker compose pull
docker compose up -d

echo "[deploy] Deploying glance..."
cd "$REPO_DIR/docker/glance"
docker compose pull
docker compose up -d

echo "[deploy] Deploying n8n..."
cd "$REPO_DIR/docker/n8n"
docker compose pull
docker compose up -d

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

echo "[deploy] Cleaning up old images..."
docker image prune -f

CLOUDFLARED_TEMPLATE="$REPO_DIR/configs/cloudflared/config.yml.template"
CLOUDFLARED_RENDERED="$REPO_DIR/configs/cloudflared/config.yml"

if [[ -f "$CLOUDFLARED_TEMPLATE" ]]; then
  echo "[deploy] Rendering cloudflared config from template..."
  envsubst < "$CLOUDFLARED_TEMPLATE" > "$CLOUDFLARED_RENDERED"

  echo "[deploy] Copying cloudflared config to /etc/cloudflared/config.yml..."
  sudo mkdir -p /etc/cloudflared
  sudo cp "$CLOUDFLARED_RENDERED" /etc/cloudflared/config.yml
  sudo chown root:root /etc/cloudflared/config.yml
  sudo chmod 640 /etc/cloudflared/config.yml

  echo "[deploy] Restarting cloudflared service..."
  sudo systemctl restart cloudflared
else
  echo "[deploy] WARNING: cloudflared template not found at $CLOUDFLARED_TEMPLATE, skipping."
fi

echo "[deploy] Done."
