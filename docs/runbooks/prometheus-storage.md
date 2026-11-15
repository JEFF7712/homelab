# Prometheus local storage migration

Prometheus uses a 20 GiB `Retain` local NVMe PV on homelab-05 at
`/persist/prometheus`, with 7-day/9-GB retention. Its node affinity follows the PV;
losing that node temporarily stops metrics collection until it is recovered or a
replacement PV is provisioned. The TSDB no longer depends on NFS availability.
The old NFS PVC is retained for rollback and is not pruned by this migration.

Deploy the fleet declaration first so homelab-05 has the writable directory
(`1000:2000`, mode 0750), then apply the GitOps PV and Helm values. The new claim
name avoids mutating or deleting the old claim. The init-container gate refuses
to start an empty TSDB before migration. An immutable StatefulSet-template error
is expected until the controlled recreation below.

After checking the live source claim/server and local NVMe capacity, run from
the pinned environment with this cluster's kubeconfig:

```sh
HOMELAB_AUTHORIZED=1 nix develop ./flake -c python -m scripts.prometheus_storage_migrate
```

The script validates identities and PV placement, suspends the owning Flux Kustomization
and Helm release, pauses the Prometheus
operator resource, scales Prometheus to zero, and waits for its pod to disappear.
A non-root Job copies the stopped TSDB from the read-only old claim to the new
claim, comparing SHA-256 of every regular file. Only an empty destination is
accepted. The copy writes a completion marker only after verification; Prometheus
cannot start without it. Both block files and WAL/head data are copied. Collection
and rule evaluation pause during the copy; the old TSDB remains unchanged.

After the Job succeeds, the script deletes only the empty StatefulSet with orphan
cascading (neither PVC), unpauses reconciliation, and waits for Prometheus to run
on homelab-05. Check `/-/ready`, a historical query from before migration, current
`up` results, rule loading and an actual alert delivery. Check the copy Job logs
and node/PV placement. Keep the old claim and Job evidence until acceptance.

On failure the script leaves reconciliation paused and both datasets intact.
Read the Job logs and resolve the cause. A completed Job can be reused when its source and target PVC UIDs still match
when resuming; a failed partial destination requires explicit inspection before
anything is removed. The script never empties a partial destination automatically.

To roll back, suspend the Helm release and pause Prometheus, stop its pod, restore
the prior Helm `storageSpec` using the original NFS claim template name and remove
the migration init container. Delete only the empty StatefulSet with orphan
cascading, then resume reconciliation. The old NFS claim rebinds unchanged.
Collection since cutover is on the local PV; preserve it before rollback if that
history matters. Complete rollback through Git and retain both datasets.
