#!/usr/bin/env bash
# Refresh registry/observed-images.json from the live cluster before policy
# checks run, so rollouts cannot fail the gate on a stale committed snapshot.
# Falls back to the committed snapshot with a warning when no cluster is
# reachable (offline laptop, shared CI runners). Always exits 0; the check
# itself remains the enforcing step.
set -uo pipefail
cd "$(dirname "$0")/../.."

refresh() {
  python -m scripts.registry snapshot-live >/dev/null
}

if command -v kubectl >/dev/null 2>&1 \
  && kubectl cluster-info --request-timeout=5s >/dev/null 2>&1; then
  refresh
  exit 0
fi

# NAS CI runners reach the cluster through passwordless sudo k3s kubectl.
if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
  shim_dir="$(mktemp -d)"
  printf '#!/bin/sh\nexec sudo -n k3s kubectl "$@"\n' >"$shim_dir/kubectl"
  chmod +x "$shim_dir/kubectl"
  if PATH="$shim_dir:$PATH" kubectl cluster-info --request-timeout=5s >/dev/null 2>&1; then
    export PATH="$shim_dir:$PATH"
    refresh
    exit 0
  fi
fi

echo "registry: WARNING: no reachable cluster; validating against committed snapshot" >&2
exit 0
