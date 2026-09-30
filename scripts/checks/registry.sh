#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
if ! command -v python >/dev/null; then
  echo "missing required tool: python; run nix develop ./flake" >&2
  exit 127
fi
python -m scripts.registry check --lock registry/images.lock.json
python -m scripts.registry plan --lock registry/images.lock.json >/dev/null
