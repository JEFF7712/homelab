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
  one per node and cluster, under `/persist/postgres/cnpg/<cluster>-<n>` on
  the node (`kubectl get pv | rg postgres`).
- **Services:** CNPG creates `<cluster>-rw`, `-ro` and `-r`. The legacy names
  `immich-postgres` and `home-assistant-postgres` select the current primary and
  are what the applications and dump jobs use.
- **Replication:** streaming replication only. There is no WAL archive
  (`spec.backup` is unset), so point-in-time recovery is unavailable and the
  recovery point of a full loss is the last dump.
- **Local dumps:** `immich-db-dump` (02:00 UTC) and `home-assistant-db-dump`
  (02:30 UTC) and `umami-db-dump` (02:45 UTC) write `pg_dump --format=custom` files, gzipped, to the `db-dumps`
  NFS volume on nas-01 (`/tank/cluster/backups-db-dumps-pvc-*/`), 7-day retention.
- **Offsite:** `dumps-to-r2` (03:00 UTC) snapshots that volume with restic to
  `s3:https://5d99d63dea23dde67ccb07e5dfb31107.r2.cloudflarestorage.com/homelab-longhorn-backup/restic-dumps`.
  Credentials are `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and
  `RESTIC_PASSWORD` in `gitops/secrets/homelab-values.sops.yaml`.

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
3. Delete the broken instance's PVC and pod. CNPG then plans a new instance.
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
5. Wait for the new instance (`immich-cluster-3`) to finish its join job and
   for the cluster to report `READY 2`.

---

## Scenario B: data corruption or rollback (nodes healthy)

1. Stop the application:
   ```sh
   kubectl -n immich scale deploy immich-server --replicas=0
   kubectl -n home-assistant scale deploy home-assistant --replicas=0
   ```
2. Pick the dump on nas-01 and decompress a copy:
   ```sh
   ssh nas-01 'ls -la /tank/cluster/backups-db-dumps-pvc-*/'
   ssh nas-01 'gunzip -kc /tank/cluster/backups-db-dumps-pvc-*/immich-YYYYMMDD.dump.gz > /tmp/restore.dump'
   ```
3. Copy it into the current primary and restore:
   ```sh
   primary=$(kubectl -n immich get cluster immich-cluster -o jsonpath='{.status.currentPrimary}')
   scp nas-01:/tmp/restore.dump /tmp/restore.dump
   kubectl -n immich cp /tmp/restore.dump "$primary":/var/lib/postgresql/data/restore.dump -c postgres
   kubectl -n immich exec "$primary" -c postgres -- \
     pg_restore --clean --if-exists --no-owner -d immich /var/lib/postgresql/data/restore.dump
   kubectl -n immich exec "$primary" -c postgres -- rm /var/lib/postgresql/data/restore.dump
   ```
   The replica follows through streaming replication.
4. Scale the application back to one replica.

---

## Scenario C: a node is lost (`homelab-01` or `homelab-05`)

CNPG promotes the surviving instance automatically, so the applications keep
working on one instance.

1. Rebuild the host from `flake/hosts/<host>/` with `nixos-anywhere`, following
   [NixOS host deployment](nixos-host-deployment.md). Confirm the target and
   recovery plan before provisioning disks.
2. Recreate the empty instance directories. They are not declared in the host
   configuration; match the live ones (`root:26`, mode `2777`, CNPG runs as
   UID 26):
   ```sh
   ssh homelab-01 'sudo install -d -o 0 -g 26 -m 2777 /persist/postgres/cnpg/immich-postgres-1 /persist/postgres/cnpg/ha-postgres-1'
   ```
3. Once the node is `Ready`, rebuild the missing instance as in Scenario A.

---

## Scenario D: total site loss (restore from R2)

1. Decrypt the three R2 values from `gitops/secrets/homelab-values.sops.yaml`.
2. Restore the dumps:
   ```sh
   export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... RESTIC_PASSWORD=...
   export RESTIC_REPOSITORY="s3:https://5d99d63dea23dde67ccb07e5dfb31107.r2.cloudflarestorage.com/homelab-longhorn-backup/restic-dumps"
   restic snapshots
   restic restore latest --target /tmp/restored-dumps
   ```
3. Once the clusters exist again (Flux recreates them empty from
   `postgres-cluster.yaml`), restore the newest dump as in Scenario B.
