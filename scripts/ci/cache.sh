#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
flake=${1:-./flake}

cache_work=$(mktemp -d "${TMPDIR:-/tmp}/attic-ci-XXXXXX")
trap 'rm -rf "$cache_work"' EXIT
export XDG_CONFIG_HOME="$cache_work/config"
mkdir -p "$XDG_CONFIG_HOME"

nix build --no-link --print-out-paths \
  "$flake#nixosConfigurations.nas-01.config.system.build.toplevel" \
  "$flake#nixosConfigurations.adguard-netbird-01.config.system.build.toplevel" \
  "$flake#nixosConfigurations.homelab-01.config.system.build.toplevel" \
  "$flake#nixosConfigurations.homelab-02.config.system.build.toplevel" \
  "$flake#nixosConfigurations.homelab-03.config.system.build.toplevel" \
  "$flake#nixosConfigurations.homelab-04.config.system.build.toplevel" \
  "$flake#nixosConfigurations.homelab-05.config.system.build.toplevel" \
  "$flake#checks.x86_64-linux.repository-contract" \
  "$flake#checks.x86_64-linux.agent-workspace-network" \
  "$flake#checks.x86_64-linux.agent-workspace-packet-flow" \
  "$flake#checks.x86_64-linux.agent-workspace-host-reservations" \
  "$flake#checks.x86_64-linux.agent-workspace-libvirt-normalization" \
  "$flake#checks.x86_64-linux.zot-registry" \
  "$flake#devShells.x86_64-linux.default" > "$cache_work/paths"

if [[ -z "${ATTIC_TOKEN:-}" ]]; then
  echo "Build succeeded; ATTIC_TOKEN is not set, so upload is skipped."
  exit 0
fi
attic login local http://10.0.30.20:8080/ "$ATTIC_TOKEN"
attic push local:homelab --stdin < "$cache_work/paths"
