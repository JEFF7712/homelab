#!/usr/bin/env bash
# Snapshot or restore the live Hearth dashboard document.
#
# The Hearth visual editor is the primary author; the PVC holds the live
# hearth.yaml. Snapshot copies it into Git for history and disaster recovery;
# restore copies it back. Reload Hearth in the browser afterwards. Restoring
# overwrites whatever the editor last saved, so snapshot first if unsure.
set -euo pipefail

SNAPSHOT="gitops/hearth/dashboard.yaml"

pod() {
  kubectl get pods -n hearth -o 'jsonpath={.items[0].metadata.name}'
}

case "${1:-}" in
  snapshot)
    kubectl exec -n hearth "$(pod)" -- cat /app/data/hearth.yaml >"$SNAPSHOT"
    echo "snapshotted live hearth.yaml to $SNAPSHOT; review and commit"
    ;;
  restore)
    kubectl exec -n hearth "$(pod)" -i -- tee /app/data/hearth.yaml <"$SNAPSHOT" >/dev/null
    echo "restored $SNAPSHOT to live Hearth; reload the browser"
    ;;
  *)
    echo "usage: $0 snapshot|restore" >&2
    exit 2
    ;;
esac
