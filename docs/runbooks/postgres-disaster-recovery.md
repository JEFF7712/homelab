# PostgreSQL Disaster Recovery

## Architecture

Home Assistant, Immich and Umami run on CloudNativePG clusters with two instances each:

| Cluster | Manifest | Image | Database/owner |
| --- | --- | --- | --- |
| `home-assistant/home-assistant-cluster` | `gitops/home-assistant/postgres-cluster.yaml` | `postgresql:17` | `homeassistant` |
| `immich/immich-cluster` | `gitops/immich/postgres-cluster.yaml` | `cloudnative-vectorchord:16` | `immich` |
| `analytics/umami-cluster` | `gitops/analytics/postgres-cluster.yaml` | `postgresql:17` | `umami` |

- **Placement:** pod anti-affinity across `homelab-01` (preferred primary) and
  `homelab-05`. Nothing else may run an instance.
- **Storage:** static `local-nvme` PersistentVolumes with `reclaimPolicy: Retain`,
  one per node and cluster. Paths use application-specific directory names,
  not the CNPG Cluster name; inspect the PV's `spec.local.path` and node affinity
  (`kubectl get pv | rg postgres`).
- **Services:** CNPG creates `<cluster>-rw`, `-ro` and `-r`. The legacy names
  `immich-postgres` and `home-assistant-postgres` select the current primary and
  are what the applications and dump jobs use.
- **Replication:** streaming replication only. There is no WAL archive
  (`spec.backup` is unset), so point-in-time recovery is unavailable and the
  recovery point of a full loss is the last dump.
- **Local dumps:** `immich-db-dump` (02:00) and `home-assistant-db-dump`
  (02:30) and `umami-db-dump` (02:45) write `pg_dump --format=custom` files, gzipped, to the `db-dumps`
  NFS volume on nas-01 (`/tank/cluster/backups-db-dumps-pvc-*/`), 7-day retention.
- **Offsite:** `dumps-to-r2` (03:00) snapshots that volume with restic to
  `s3:https://5d99d63dea23dde67ccb07e5dfb31107.r2.cloudflarestorage.com/homelab-longhorn-backup/restic-dumps`.
  Credentials are `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and
  `RESTIC_PASSWORD` in `gitops/secrets/homelab-values.sops.yaml`.

These dump/upload and restore-drill CronJobs omit `spec.timeZone`: their cron
expressions use the controller manager's local timezone, not an explicitly
declared UTC schedule. Verify the controller timezone and actual Job timestamps
when checking backup ordering. See [CronJob time zones](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/#time-zones).

The static data directories on homelab-01 and homelab-05 are:

| Application | homelab-01 | homelab-05 | PV declarations |
| --- | --- | --- | --- |
| Home Assistant | `ha-postgres-1` | `ha-postgres-2` | `gitops/home-assistant/volumes.yaml` |
| Immich | `immich-postgres-1` | `immich-postgres-2` | `gitops/immich/postgres-volume.yaml` |
| Umami | `umami-postgres-1` | `umami-postgres-2` | `gitops/analytics/postgres-volumes.yaml` |

All paths above are below `/persist/postgres/cnpg/`. PVC instance names can
change after replacement; derive their PV, directory and node from fresh
metadata rather than assuming the instance suffix names the directory.

Check health with `kubectl get clusters.postgresql.cnpg.io -A`. A healthy
cluster reports `Cluster in healthy state` with `READY 2`.

---

## Scenario A: a replica will not rejoin

Symptom: one instance stays `0/1` and its log repeats `Failed to execute
pg_rewind` (for example `could not find common ancestor of the source and
target cluster's timelines`). The primary keeps serving, but the cluster has no
standby until the replica is rebuilt from the primary.

1. Confirm which instance is the primary and that it is healthy:
   ```sh
   kubectl -n immich get cluster immich-cluster -o jsonpath='{.status.currentPrimary}{"\n"}'
   kubectl -n immich get pods -l cnpg.io/cluster=immich-cluster -o wide
   ```
2. Note the broken instance's PVC and PV (for example `immich-cluster-2` on
   `immich-postgres-nvme-05`). Never touch the primary's.
3. Confirm the broken instance is not serving as primary and stop/fence its
   process before reusing its directory. If the old host is unreachable, fence
   it before proceeding. Delete only the broken instance's PVC and pod. CNPG
   then plans a replacement; preserve the retained data for investigation.
   ```sh
   kubectl -n immich delete pvc immich-cluster-2 --wait=false
   kubectl -n immich delete pod immich-cluster-2
   ```
4. The PV is `Retain`, so it becomes `Released` with the old data still on
   disk. Move that data aside rather than deleting it, then release the claim
   so the new instance's PVC can bind the empty directory:
   ```sh
   ssh homelab-05 'cd /persist/postgres/cnpg && sudo mv immich-postgres-2 immich-postgres-2.diverged-$(date +%Y%m%d) && sudo install -d -o 0 -g 26 -m 2777 immich-postgres-2'
   kubectl patch pv immich-postgres-nvme-05 --type=json -p '[{"op":"remove","path":"/spec/claimRef"}]'
   ```
   A replica that cannot `pg_rewind` was usually a primary on the old
   timeline after the failover, so it can hold writes the new primary never
   received. Compare `pg_controldata` on both: if the old instance's latest
   checkpoint is past the fork point in the primary's newest `.history` file,
   keep the moved copy until those writes are judged expendable or recovered.
5. Read the replacement instance/PVC name from CNPG status; do not assume it
   will be `immich-cluster-3`. If the owning PV manifest reserves a `claimRef`
   (as Umami does), reconcile that source reservation with the intended new
   claim before rebinding. Wait for the join job and `READY 2`, then verify
   replica health and application reads.

---

## Scenario B: data corruption or rollback (nodes healthy)

This replaces database contents. Review the selected snapshot and isolated
restore result, retain the current database for recovery, and obtain the
exact-change approval for intended persistent-data destruction. Coordinate Flux
so it cannot restart writers while restoration is in progress. The commands
below are an Immich example; select the affected application only:

| Database | Application writer to stop |
| --- | --- |
| `immich` | `immich/immich-server` |
| `homeassistant` | `home-assistant/home-assistant` |
| `umami` | `analytics/umami` |

1. Stop the selected application and confirm its connections have closed:
   ```sh
   HOMELAB_AUTHORIZED=1 kubectl -n immich scale deploy immich-server --replicas=0
   ```
2. Pick the dump on nas-01 and decompress a copy:
   ```sh
   ssh nas-01 'ls -la /tank/cluster/backups-db-dumps-pvc-*/'
   ssh nas-01 'gunzip -kc /tank/cluster/backups-db-dumps-pvc-*/immich-YYYYMMDD.dump.gz > /tmp/restore.dump'
   ```
3. Inspect the archive with `pg_restore --list` and rehearse the restore into
   an isolated compatible PostgreSQL image, including required extensions and
   roles. Copy the accepted archive into the current primary and restore:
   ```sh
   primary=$(kubectl -n immich get cluster immich-cluster -o jsonpath='{.status.currentPrimary}')
   scp nas-01:/tmp/restore.dump /tmp/restore.dump
   kubectl -n immich cp /tmp/restore.dump "$primary":/var/lib/postgresql/data/restore.dump -c postgres
   kubectl -n immich exec "$primary" -c postgres -- \
     pg_restore --clean --if-exists --exit-on-error -U postgres -d immich /var/lib/postgresql/data/restore.dump
   kubectl -n immich exec "$primary" -c postgres -- rm /var/lib/postgresql/data/restore.dump
   ```
   The replica follows through streaming replication. The production restore
   preserves archive ownership, so the expected owner roles must exist. Using
   `--no-owner` as the superuser would make restored objects belong to that
   restoring user; the disposable drill uses it for a different purpose.
   `--exit-on-error` stops at the first SQL failure rather than continuing a
   partial restore. `--clean` drops archive-listed objects; it does not remove
   newer objects absent from the dump. For an exact schema rollback, rehearse
   a fresh-database restore plan before production. See [pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html).
4. Verify schema, object ownership, expected data and replica health before
   restoring the application to its declared replica count and resuming Flux.
   Verify application reads and migrations; retain failed restore evidence.

---

## Scenario C: a node is lost (`homelab-01` or `homelab-05`)

CNPG promotes the surviving instance automatically, so the applications keep
working on one instance.

1. Rebuild the host from `flake/hosts/<host>/` with `nixos-anywhere`, following
   [NixOS host deployment](nixos-host-deployment.md). Confirm the target and
   recovery plan before provisioning disks.
2. Recreate the empty instance directories listed in the PV declarations,
   including Umami. They are not declared in the host configuration; match the
   required ownership (`root:26`, mode `2777`, CNPG runs as UID 26). For
   homelab-01:
   ```sh
   ssh homelab-01 'sudo install -d -o 0 -g 26 -m 2777 /persist/postgres/cnpg/immich-postgres-1 /persist/postgres/cnpg/ha-postgres-1 /persist/postgres/cnpg/umami-postgres-1'
   ```
   For homelab-05, use the corresponding `*-2` directories from the table. Do
   not overwrite an occupied directory; inspect and preserve its data first.
3. Once the node is `Ready`, rebuild the missing instance as in Scenario A.

---

## Scenario D: total site loss (restore from R2)

1. Load the three R2 values from `gitops/secrets/homelab-values.sops.yaml` into
   the restore process environment without printing them; keep independent
   access to the decryption key and password. Follow [secret handling](secrets.md).
2. Restore the dumps:
   ```sh
   export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... RESTIC_PASSWORD=...
   export RESTIC_REPOSITORY="s3:https://5d99d63dea23dde67ccb07e5dfb31107.r2.cloudflarestorage.com/homelab-longhorn-backup/restic-dumps"
   restic snapshots
   restic restore latest --target /tmp/restored-dumps
   ```
3. Once the clusters exist again (Flux recreates them empty from
   `postgres-cluster.yaml`), restore the newest dump as in Scenario B.

## Restore drills

Two CronJobs schedule weekly restore tests into scratch space. A schedule is
not proof of recovery; inspect the most recent completed Job and per-dataset
result and pass timestamps before relying on a backup:

- `backups/restore-drill` (Sunday 05:30, controller timezone): `restic check --read-data-subset=5%`
  on the R2 dump repository, requires a snapshot from the last two days,
  restores it, and loads each dump into a throwaway postgres that must come up
  with tables and rows. Immich's dump loads in Immich's postgres image for its
  vector extensions.
- `immich/library-restore-drill` (Sunday 06:00, controller timezone): samples 2% of the local
  library repository's packs, requires a recent snapshot and restores a slice
  of it.

Each writes per-dataset `<dataset>.result`, `.checked` and `.passed` (epoch
seconds) to the `restore-drill-evidence` ConfigMap in its namespace and fails
the Job when any dataset fails. `cluster-sentinel` alerts through ntfy when a
dataset has no pass within eight days or its latest result failed.
`tests/test_restore_drills.py` fails when a dump or restic backup has no drill
or the sentinel's list drifts from the drills.

Run a drill now (after changing a backup or before destroying data):

```sh
drill_job=restore-drill-manual-$(date +%s)
HOMELAB_AUTHORIZED=1 kubectl -n backups create job --from=cronjob/restore-drill "$drill_job"
kubectl -n backups wait --for=condition=complete "job/$drill_job" --timeout=2h
kubectl -n backups get configmap restore-drill-evidence -o yaml
```

Confirm the evidence was written by that run: match its completion window,
per-dataset `.checked`, `.result` and `.passed` fields. If the wait fails, inspect
the Job and logs; an older pass does not make this attempt successful.
Persistent-data destruction retains the operator approval boundary. Show a
recent restore pass for the affected dataset and an independent recovery copy
before proposing the exact destructive change.
