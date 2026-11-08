#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
if ! command -v python >/dev/null; then
  echo "missing required tool: python; run nix develop ./flake" >&2
  exit 127
fi
python -m scripts.registry check --lock registry/images.lock.json
python -m scripts.registry plan --lock registry/images.lock.json >/dev/null

# A change may reference only upstream digests main has already imported
# (scripts/registry/ordering.py). Pull requests carry their merge base; local
# runs compare against origin/main. Main pushes and tags have no PR base.
base="${CI_MERGE_BASE:-}"
if [[ -z "$base" && -z "${CI:-}" ]]; then
  base="$(git merge-base origin/main HEAD 2>/dev/null || true)"
fi
if [[ -n "$base" ]] && git cat-file -e "$base^{commit}" 2>/dev/null; then
  python -m scripts.registry.ordering --base "$base"
fi
