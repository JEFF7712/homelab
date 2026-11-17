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
- **Replication:** streaming replication between the two instances.
- **WAL archive (Umami only):** `analytics/umami-cluster` archives WAL and
  takes weekly base backups to Garage on nas-01; see
  [WAL archive on Garage](#wal-archive-on-garage). Home Assistant and Immich
  have no WAL archive, so point-in-time recovery is unavailable for them and
  the recovery point of a full loss is the last dump.
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

The Restic dump job uses stable host `homelab-db-dumps`, tag `db-dumps-v2`
and `--group-by host,tags`; pod hostnames are not stable retention identities.
Only newly tagged snapshots are aged out by the corrected policy. Existing
legacy snapshots remain available for a separate reviewed cleanup. Inspect
the configured `forget` command with `--dry-run` before any legacy migration.

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

---

## WAL archive on Garage

`analytics/umami-cluster` is the pilot for continuous WAL archiving through
the CNPG-I Barman Cloud plugin (`plugin-barman-cloud` v0.16.0 in
`cnpg-system`, `gitops/platform/cnpg/barman-cloud-plugin.yaml`). Its
`ObjectStore` `analytics/umami-garage` (`gitops/analytics/wal-archive.yaml`)
writes to `s3://cnpg-wal/analytics/umami-cluster` on the Garage S3 service
(`http://10.0.30.20:3900`, region `homelab`, path-style addressing).

- **What it gives:** point-in-time recovery inside a 14-day window
  (`retentionPolicy: 14d`) and a recovery point about 5 minutes old
  (CNPG's `archive_timeout`), instead of the last nightly dump. Base backups
  run Sunday 04:00 UTC (`ScheduledBackup` `umami-cluster-weekly`).
- **What it does not give:** protection against losing nas-01. The archive
  sits on the same `tank` pool as the dumps. The hourly platform backup copies
  `tank/s3` (all of Garage) to R2, so an offsite copy lags by about an hour,
  but using it means restoring Garage first. The logical dumps to R2
  (Scenario D) remain the offsite copy of record.
- **Transport:** Garage serves plain HTTP on the LAN. SigV4 never sends the
  secret key, but WAL content crosses the server VLAN unencrypted, as the
  NFS dumps already do.
- **Credentials:** Garage key `cnpg-wal-umami` (read/write on bucket
  `cnpg-wal` only) was created by the operator on nas-01; that needs root
  there because Garage has no admin API configured. Its SOPS file
  `gitops/secrets/cnpg-wal-archive.sops.yaml` becomes Secret
  `secret-source/cnpg-wal-archive`, projected into `homelab-analytics-values`
  and copied to `analytics/cnpg-wal-archive` by the `homelab-analytics` store
  (`gitops/eso/domain-stores.yaml`). Rotate by creating a new key on nas-01,
  replacing the SOPS file, and revoking the old key once `ContinuousArchiving`
  is `True` with the new one.
- **Switchover:** `switchoverDelay: 120` caps how long a switchover waits for
  the old primary to archive its last segment; the CNPG default of an hour
  would hold writes that long while the NAS is down.

Check it:

```sh
kubectl -n analytics get cluster umami-cluster \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.message}{"\n"}{end}'
kubectl -n analytics get objectstore umami-garage -o jsonpath='{.status}'; echo
kubectl -n analytics get backups.postgresql.cnpg.io
```

Alerts (`gitops/backups/wal-archive-alerts.yaml`): `WalArchivingFailing`
(segments waiting on the primary for 30 minutes), `WalArchiveBacklogHigh`
(local `pg_wal` above 2.5 GiB), `WalArchiveBaseBackupStale` (no base backup
in eight days) and `WalArchiveBackupMetricMissing`.

### When the NAS is unreachable

Postgres keeps every segment it cannot archive in `pg_wal` and keeps serving.
The Umami volume is a directory on the node's `/persist` disk without a quota,
so the hard limit is that disk, shared with everything else on the node. The
budget is the declared 5 GiB: at the measured average of 19 segments a day
(16 MiB each, mostly `archive_timeout` padding) that is about 14 days, about
7 at the busiest observed day. The hourly platform backup stops Garage for a
few seconds; archiving retries and catches up without action.

If `WalArchiveBacklogHigh` fires and the NAS will not be back before the
backlog nears 5 GiB, stop archiving rather than let the node disk fill:

1. Remove the `plugins` entry from `gitops/analytics/postgres-cluster.yaml`
   and ship. The instances restart once without the sidecar and Postgres
   recycles `pg_wal` normally. The archive keeps everything up to the outage;
   point-in-time recovery has a gap from then on.
2. When Garage is back, restore the `plugins` entry and ship, then take a base
   backup at once so recovery does not depend on WAL across the gap:
   ```sh
   HOMELAB_AUTHORIZED=1 kubectl create -f - <<'YAML'
   apiVersion: postgresql.cnpg.io/v1
   kind: Backup
   metadata:
     generateName: umami-cluster-after-outage-
     namespace: analytics
   spec:
     cluster:
       name: umami-cluster
     method: plugin
     pluginConfiguration:
       name: barman-cloud.cloudnative-pg.io
   YAML
   ```

### Restore from the WAL archive

Restore into a new Cluster; never point a recovery at the production
cluster's name or volumes. The weekly drill does exactly this, so the
`umami-wal-drill-cluster` ConfigMap in `gitops/backups/wal-drill.yaml` is a
tested template. For point-in-time recovery, add a target:

```yaml
bootstrap:
  recovery:
    source: origin
    database: umami
    owner: umami
    recoveryTarget:
      targetTime: "2026-11-15 12:00:00+00"
externalClusters:
  - name: origin
    plugin:
      name: barman-cloud.cloudnative-pg.io
      parameters:
        barmanObjectName: umami-garage
        serverName: umami-cluster
```

Give the restored cluster no `plugins` archiver entry, or a different
`serverName`, so it cannot write into the production archive. Verify the
restored data, then move the application to it or copy the needed rows back
(Scenario B covers stopping writers). Replacing the production cluster's data
is persistent-data destruction and keeps that approval boundary.

### Rolling out to Home Assistant and Immich

Each needs its own Garage key created on nas-01 by the operator and its SOPS
file, a projection property and store for its namespace, an `ObjectStore`, the
`plugins` entry and `switchoverDelay`, a `ScheduledBackup`, a drill dataset,
and alert selectors widened to its namespace. Enabling the plugin restarts the
instances once. Home Assistant forces about 289 segments a day (4.5 GiB of
local `pg_wal` a day during an outage, about two days of its 10 GiB budget),
so raise its `archive_timeout` first; Immich (about 64 a day, 20 GiB) has
about 19 days.

## Restore drills

Three CronJobs schedule weekly restore tests into scratch space. A schedule is
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
- `analytics/umami-wal-restore-drill` (Monday 05:00, controller timezone):
  restores the scratch Cluster `umami-wal-drill` (NFS volume) from the latest
  base backup plus archived WAL, requires the same `website_event` count as
  production up to 15 minutes before the run, deletes the scratch cluster and
  records dataset `umami-wal` in `analytics/restore-drill-evidence`. Run it now
  with `HOMELAB_AUTHORIZED=1 kubectl -n analytics create job --from=cronjob/umami-wal-restore-drill <name>`.

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
