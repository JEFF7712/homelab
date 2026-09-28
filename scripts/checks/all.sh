#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export SKIP_TESTS=1
export SKIP_NIX_EVAL=1
export CHECK_FROM_ALL=1
bash scripts/checks/agent-workflows.sh
bash scripts/checks/python.sh
bash scripts/checks/registry.sh
bash scripts/checks/nix.sh all
bash scripts/checks/gitops.sh
bash scripts/checks/tofu.sh
bash scripts/checks/home-assistant.sh
python scripts/checks/docs.py
python scripts/checks/whitespace.py
if [[ "${SKIP_SECRET_SCAN:-0}" != "1" ]]; then
  gitleaks detect --source . --redact
fi
if [[ "${SKIP_FLAKE_CHECK:-0}" != "1" ]]; then
  nix flake check 'path:.?dir=flake' --no-write-lock-file
fi
