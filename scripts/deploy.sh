#!/usr/bin/env bash
set -e

REPO_DIR="$HOME/homelab"

cd "$REPO_DIR"
echo "[deploy] Pulling latest changes..."
git pull --ff-only

set -a
source $HOME/homelab/.env
set +a

envsubst < "$REPO_DIR/configs/cloudflared/config.yml.template" \
  > "$REPO_DIR/configs/cloudflared/config.yml"

sudo cp "$REPO_DIR/configs/cloudflared/config.yml" /etc/cloudflared/config.yml
sudo systemctl restart cloudflared

echo "[deploy] Deploying main stack..."
cd "$REPO_DIR/docker"
docker compose pull
docker compose up -d

echo "[deploy] Done."
