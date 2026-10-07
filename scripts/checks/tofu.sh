#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
command -v tofu >/dev/null || { echo 'missing required tool: tofu; run nix develop ./flake' >&2; exit 127; }
for stack in tofu/*/; do
  if [[ "${CI_LINT_EXTERNAL:-0}" != "1" ]]; then tofu -chdir="$stack" fmt -check; fi
  if [[ ! -d "${stack}.terraform/providers" ]]; then
    echo "OpenTofu providers are not provisioned for $stack; run just provision-check-deps" >&2
    exit 69
  fi
done
# Stack validates are independent; run them concurrently. PIDs are
# collected explicitly because a bare `wait` always succeeds and would
# swallow a validation failure.
pids=()
for stack in tofu/*/; do
  tofu -chdir="$stack" validate &
  pids+=($!)
done
rc=0
for pid in "${pids[@]}"; do
  wait "$pid" || rc=1
done
exit "$rc"
