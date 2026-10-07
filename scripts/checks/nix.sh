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
  # One process evaluates every host's toplevel derivation graph.
  # nix eval accepts a single installable, so nix build --dry-run
  # evaluates all targets in one flake evaluation without building
  # or fetching anything. The plan goes to stderr and is only
  # surfaced when the gate fails.
  eval_targets=()
  for host in "${hosts[@]}"; do
    eval_targets+=(
      "path:.?dir=flake#nixosConfigurations.${host}.config.system.build.toplevel"
    )
  done
  plan_err="$(mktemp)"
  trap 'rm -f "$plan_err"' EXIT
  if ! nix build --dry-run --no-link "${eval_targets[@]}" 2>"$plan_err"; then
    cat "$plan_err" >&2
    exit 1
  fi
fi
