#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Optional: pass WIFI_SSID and WIFI_PASSWORD in the environment to pre-bake
# credentials into build/pico_w_hub75_receiver.uf2. Otherwise provision over
# /dev/ttyACM0 after flashing with: WIFI <ssid>\t<password>
SSID_ARG="${WIFI_SSID:-${CIRCUITPY_WIFI_SSID:-}}"
PASS_ARG="${WIFI_PASSWORD:-${CIRCUITPY_WIFI_PASSWORD:-}}"

nix develop . -c bash -c '
  env -u CC -u CXX cmake -B build -G Ninja \
    -DPICO_BOARD=pico_w \
    -DWIFI_SSID="$1" \
    -DWIFI_PASSWORD="$2"
  env -u CC -u CXX cmake --build build
' _ "$SSID_ARG" "$PASS_ARG"

echo "Built UF2: $(pwd)/build/pico_w_hub75_receiver.uf2"
