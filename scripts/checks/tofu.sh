#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
command -v tofu >/dev/null || { echo 'missing required tool: tofu; run nix develop ./flake' >&2; exit 127; }
python -m scripts.opnsense_config --check
for stack in tofu/*/; do
  if [[ "${CI_LINT_EXTERNAL:-0}" != "1" ]]; then tofu -chdir="$stack" fmt -check; fi
  if [[ ! -d "${stack}.terraform/providers" ]]; then
    # Fresh worktrees start without providers. Outside CI, provision them
    # from the shared plugin cache so only the first worktree downloads.
    if [[ -z "${CI:-}" ]]; then
      export TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/opentofu/plugins}"
      mkdir -p "$TF_PLUGIN_CACHE_DIR"
      tofu -chdir="$stack" init -backend=false -lockfile=readonly -input=false >/dev/null
    else
      echo "OpenTofu providers are not provisioned for $stack; run just provision-check-deps" >&2
      exit 69
    fi
  fi
done
python -m scripts.opnsense_config --provider-options --check
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
