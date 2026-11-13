#!/usr/bin/env bash
set -euo pipefail

retry() {
  local -r max_attempts=15
  local -r delay=4
  local attempt=1
  until "$@"; do
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "[lidarr-boot] ERROR: Command failed after $attempt attempts: $*" >&2
      return 1
    fi
    echo "[lidarr-boot] Command failed (attempt $attempt/$max_attempts), retrying in ${delay}s: $*" >&2
    sleep "$delay"
    attempt=$((attempt + 1))
  done
}

echo "--- [lidarr-boot] checking runtime deps ---"
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1 || ! command -v flac >/dev/null 2>&1; then
  retry apk update
  retry apk add --no-cache ffmpeg python3 py3-pip jq flac
fi
command -v ffprobe >/dev/null 2>&1 || true

echo "--- [lidarr-boot] extended toolchain (pre-installed or self-skipping) ---"
if ! command -v deemix >/dev/null 2>&1 || ! command -v xq >/dev/null 2>&1; then
  rm -f /config/setup_version.txt
  if [ -f /config/setup.bash ]; then
    bash /config/setup.bash
  fi
elif [ -f /config/setup.bash ]; then
  bash /config/setup.bash
fi

echo "--- [lidarr-boot] tidal backend (tidaler + tidal-dl compat shim) ---"
# tidaler is pre-baked in the image; fallback to dynamic install if missing
if ! command -v tidaler >/dev/null 2>&1; then
  retry uv pip install --system --no-cache-dir --break-system-packages "tidaler==0.1.8"
fi
if [ -f /tmp/lidarr-extended/tidal-dl-shim ]; then
  cp /tmp/lidarr-extended/tidal-dl-shim /usr/local/bin/tidal-dl
  chmod 755 /usr/local/bin/tidal-dl
fi
command -v tidal-dl

# Ensure /custom-services.d/python/ARLChecker.py exists if present on PVC
mkdir -p /custom-services.d/python
if [ -f /config/custom-services.d/python/ARLChecker.py ] && [ ! -f /custom-services.d/python/ARLChecker.py ]; then
  cp -a /config/custom-services.d/python/ARLChecker.py /custom-services.d/python/ARLChecker.py
fi

# Verify critical binaries are present
for bin in ffmpeg flac jq xq deemix tidaler tidal-dl; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "[lidarr-boot] FATAL: required binary '$bin' is not installed" >&2
    exit 1
  fi
done

echo "--- [lidarr-boot] rendering extended.conf ---"
python3 /tmp/lidarr-extended/10-render-extended-conf
echo "--- [lidarr-boot] starting extended services ---"
# deemix resolves its config via XDG_CONFIG_HOME, but the container
# sets it to /config/.config while the scripts stage credentials in
# /config/xdg. Without this override deemix prompts for the ARL
# interactively and every download aborts. Scoped to the daemons so
# /init and Lidarr keep the image default environment. Invoked via
# bash explicitly because the scripts' with-contenv shebang would
# reset the override back to the container default.
mkdir -p /config/logs
(
  while true; do
    XDG_CONFIG_HOME=/config/xdg bash /config/custom-services.d/Audio
    sleep 60
  done
) >>/config/logs/Audio-supervisor.log 2>&1 &
(
  while true; do
    XDG_CONFIG_HOME=/config/xdg bash /config/custom-services.d/ARLChecker
    sleep 60
  done
) >>/config/logs/ARLChecker-supervisor.log 2>&1 &
echo "--- [lidarr-boot] exec /init ---"
exec /init
