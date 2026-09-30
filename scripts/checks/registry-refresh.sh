#!/usr/bin/env bash
# Refresh registry/observed-images.json from the live cluster before policy
# checks run, so rollouts cannot fail the gate on a stale committed snapshot.
#
# In CI an unreachable cluster is fatal rather than a warning. The check reads
# that snapshot to decide whether every image still running is pinned, so
# validating the committed copy makes the verdict a claim about the last commit
# rather than about the cluster: real drift can no longer fail the job, and
# nothing in the log says so. That is how apps/quartz stayed green while the
# cluster ran a digest the lock no longer described. Locally it stays a
# warning, because working offline is legitimate and the snapshot is then merely
# stale rather than uncheckable.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

refresh() {
  python -m scripts.registry snapshot-live >/dev/null
}

# Probed with the same read refresh performs, not `kubectl cluster-info`, which
# additionally lists services and so reports failure for an identity that is
# otherwise able to take the snapshot.
if command -v kubectl >/dev/null 2>&1 \
  && kubectl get pods,jobs,cronjobs -A -o json --request-timeout=5s \
    >/dev/null 2>&1; then
  refresh
  exit 0
fi

# Succeeds only on a host running the k3s server role: an agent keeps no
# /etc/rancher/k3s/k3s.yaml, so this falls back to localhost:8080. The CI runner
# is an agent, so the path cannot work there, which is why CI now fails loudly
# rather than reporting a snapshot check it never performed.
if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
  shim_dir="$(mktemp -d)"
  printf '#!/bin/sh\nexec sudo -n k3s kubectl "$@"\n' >"$shim_dir/kubectl"
  chmod +x "$shim_dir/kubectl"
  if PATH="$shim_dir:$PATH" kubectl get pods,jobs,cronjobs -A -o json \
    --request-timeout=5s >/dev/null 2>&1; then
    export PATH="$shim_dir:$PATH"
    refresh
    exit 0
  fi
  rm -rf "$shim_dir"
fi

if [ -n "${CI:-}" ]; then
  cat >&2 <<'EOF'
registry: ERROR: no reachable cluster in CI; refusing to validate the committed snapshot.

registry check reads registry/observed-images.json to decide whether every image
still running in the cluster is pinned. Falling back to the committed copy makes
the verdict a claim about the last commit rather than about the cluster, so real
drift can no longer fail this job and nothing in the log says so.

Fix by giving the job cluster access rather than suppressing this:
  - set KUBECONFIG to a kubeconfig whose identity may get and list pods, jobs,
    and cronjobs cluster-wide, or
  - run `just registry-check` from a host that can reach the API server and
    commit the refreshed registry/observed-images.json.
EOF
  exit 1
fi

echo "registry: WARNING: no reachable cluster; validating against committed snapshot" >&2
exit 0