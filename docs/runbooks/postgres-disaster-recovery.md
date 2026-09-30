# PostgreSQL Disaster Recovery

## Architecture & Storage Policy

PostgreSQL databases for Home Assistant and Immich operate under **Policy B** (Node-Pinned NVMe with Dual-Tier Backups):

- **Primary Node:** `homelab-01` (`10.0.30.11`)
- **Storage Type:** Node-pinned local NVMe via `hostPath` PersistentVolumes:
  - Immich: `/persist/postgres/immich` (`immich-postgres` pod in namespace `immich`)
  - Home Assistant: `/persist/postgres/home-assistant` (`home-assistant-postgres` pod in namespace `home-assistant`)
- **Local Backup (Tier 1):** Daily automated `pg_dump --format=custom` exports to the `db-dumps` NFS PersistentVolume hosted on `nas-01` (`/tank/cluster/backups-db-dumps-.../`):
  - `immich-db-dump`: Daily at 02:00 UTC (`/dumps/immich-$YYYYMMDD.dump.gz`, 7-day retention)
  - `home-assistant-db-dump`: Daily at 02:30 UTC (`/dumps/home-assistant-$YYYYMMDD.dump.gz`, 7-day retention)
- **Offsite Backup (Tier 2):** Daily encrypted restic snapshot to Cloudflare R2:
  - `dumps-to-r2`: Daily at 03:00 UTC (`s3:https://...r2.cloudflarestorage.com/homelab-longhorn-backup/restic-dumps`)

---

## Scenario A: Database Corruption or Rollback (Host Healthy)

If database tables are corrupted or inadvertently modified while `homelab-01` remains online:

1. **Stop Application Workloads:**
   ```sh
   kubectl scale deploy -n immich immich-server --replicas=0
   kubectl scale deploy -n home-assistant home-assistant --replicas=0
   ```

2. **Locate the Target Backup File on `nas-01`:**
   ```sh
   ssh nas-01 "ls -la /tank/cluster/backups-db-dumps-pvc-*/"
   ```

3. **Decompress the Desired Dump:**
   ```sh
   gunzip -k /tank/cluster/backups-db-dumps-pvc-*/immich-YYYYMMDD.dump.gz
   ```

4. **Restore into PostgreSQL Pod:**
   Copy the uncompressed dump to the pod and run `pg_restore`:
   ```sh
   kubectl cp /tank/cluster/backups-db-dumps-pvc-*/immich-YYYYMMDD.dump \
     immich/$(kubectl get pod -n immich -l app=immich-postgres -o jsonpath='{.items[0].metadata.name}'):/tmp/restore.dump

   kubectl exec -n immich -it deploy/immich-postgres -- \
     pg_restore --clean --if-exists --no-owner -U immich -d immich /tmp/restore.dump
   ```

5. **Restart Application Workloads:**
   ```sh
   kubectl scale deploy -n immich immich-server --replicas=1
   kubectl scale deploy -n home-assistant home-assistant --replicas=1
   ```

---

## Scenario B: Drive Failure or Node Replacement (`homelab-01`)

If the NVMe drive on `homelab-01` fails or the physical machine requires replacement:

1. **Replace Hardware & Provision Base System:**
   - Install replacement NVMe drive.
   - Boot the installer and provision the replacement using `nixos-anywhere`
     and the owning host/disk configuration under `flake/hosts/homelab-01/`.
     Follow the repository [deployment policy](../../README.md#deployment).
     There is no `provision-homelab-01` recipe. Confirm the replacement target
     and recovery plan before disk provisioning.

2. **Recreate Local Storage Mounts & Ownership:**
   PostgreSQL containers run as UID `70` (alpine postgres for Home Assistant) or `999` (debian postgres for Immich):
   ```sh
   ssh homelab-01 "sudo mkdir -p /persist/postgres/{immich,home-assistant} && \
     sudo chown -R 999:999 /persist/postgres/immich && \
     sudo chown -R 70:70 /persist/postgres/home-assistant && \
     sudo chmod 700 /persist/postgres/{immich,home-assistant}"
   ```

3. **Verify Node Joins K3s Cluster:**
   ```sh
   kubectl get nodes -o wide
   ```

4. **Allow PostgreSQL Pod to Initialize and Restore Backup:**
   - The PostgreSQL pod will initialize a fresh, empty cluster on the new local storage directory.
   - Perform the database restore using the steps in **Scenario A** from the latest dump on `nas-01`.

---

## Scenario C: Total Site Disaster (Restoring from Cloudflare R2)

If both `homelab-01` and `nas-01` are unavailable and backups must be recovered from Cloudflare R2:

1. **Export R2 Secrets from SOPS:**
   Retrieve `r2-access-key-id`, `r2-secret-access-key`, and `restic-password` from `secrets/backups.yaml`.

2. **Initialize Restic Environment:**
   ```sh
   export AWS_ACCESS_KEY_ID="<R2_ACCESS_KEY_ID>"
   export AWS_SECRET_ACCESS_KEY="<R2_SECRET_ACCESS_KEY>"
   export RESTIC_PASSWORD="<RESTIC_PASSWORD>"
   export RESTIC_REPOSITORY="s3:https://5d99d63dea23dde67ccb07e5dfb31107.r2.cloudflarestorage.com/homelab-longhorn-backup/restic-dumps"
   ```

3. **List & Restore Dumps:**
   ```sh
   restic snapshots
   restic restore latest --target /tmp/restored-dumps
   ```

4. **Proceed with Scenario A or B Restore:**
   Use the restored `.dump.gz` files from `/tmp/restored-dumps/dumps/` to repopulate PostgreSQL.
