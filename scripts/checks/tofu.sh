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
  tofu -chdir="$stack" validate
done
