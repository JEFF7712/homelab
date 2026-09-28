#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
for tool in nix nixfmt; do command -v "$tool" >/dev/null || { echo "missing required tool: $tool; run nix develop ./flake" >&2; exit 127; }; done
mapfile -d '' files < <(git ls-files -co --exclude-standard -z '*.nix')
if [[ "${CI_LINT_EXTERNAL:-0}" != "1" ]]; then nixfmt --check "${files[@]}"; fi
target=${1:-all}
hosts=(
  adguard-netbird-01
  nas-01
  homelab-01
  homelab-02
  homelab-03
  homelab-04
  homelab-05
  homelab-01-registry
  homelab-02-registry
  homelab-03-registry
  homelab-04-registry
  homelab-05-registry
)
if [[ $target != all ]]; then hosts=("$target"); fi
if [[ "${SKIP_NIX_EVAL:-0}" != "1" ]]; then
  for host in "${hosts[@]}"; do nix eval --no-write-lock-file "path:.?dir=flake#nixosConfigurations.${host}.config.system.build.toplevel.drvPath" >/dev/null; done
fi
