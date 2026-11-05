# External Backup & Disaster Recovery Runbook

## Architecture and Overview

This runbook documents the dedicated 1 TB external HDD backup architecture hosted on [`adguard-netbird-01`](file:///home/rupan/homelab/flake/hosts/adguard-netbird-01/default.nix), designed to survive total NAS failure, pool corruption, or ransomware.

```
┌─────────────────────────────────┐                 ┌──────────────────────────────────────┐
│             nas-01              │                 │          adguard-netbird-01          │
│                                 │                 │                                      │
│  /tank/cluster/immich-library/  │  Read-Only      │  /mnt/backups/restic-repo/           │
│  /tank/cluster/backups-db-dumps/│◄─────────────── │  (1 TB 2.5" Ext4 HDD, UUID           │
│  /tank/forgejo/repositories/    │  SSHFS / SFTP   │   00d3f54f-28f8-454c-929a-8c8191ab) │
│  /tank/forgejo/backups/         │  (backup-reader)│                                      │
│                                 │                 │  • immich-external-backup.service    │
│  • forgejo-backup-dump.timer    │                 │  • immich-external-backup.timer      │
│  • sanoid operational template  │                 │  • smartd disk monitoring            │
└─────────────────────────────────┘                 └──────────────────────────────────────┘
```

### Key Principles & Guards
1. **Pull Architecture:** `adguard-netbird-01` initiates the connection. `nas-01` has no SSH keys or write credentials to `adguard-netbird-01`, so a compromise on `nas-01` cannot delete or tamper with backups.
2. **Server-Side Forced Read-Only SFTP:** `nas-01` restricts the `backup-reader` user via OpenSSH `ForceCommand internal-sftp -R` with all port and agent forwarding disabled.
3. **Fail-Closed Safeguards:** The backup service requires `/mnt/backups` to be an active mountpoint with a verified canary (`.backup-target-canary`) and at least 15 GiB free space before running. Missing mounts or sources immediately abort to prevent writing to the 2GB root tmpfs.
4. **Freshness Verification:** The runner verifies that today's Immich and Home Assistant database dumps exist and meet minimum size thresholds, and that Forgejo's SQLite database dump file exists and is non-empty before invoking Restic.
5. **Separate Pruning & Mutual Exclusion:** Pruning runs in an independent service (`immich-external-backup-prune.service`, Sundays at 06:00) that strictly refuses to execute if a backup or check is active, if the mutual exclusion lock is held, or if the last successful backup is older than 36 hours. Automatic pruning enforces `--keep-last 3` as an invariant safeguard alongside the 14-daily, 8-weekly, and 3-monthly policy.
6. **Weekly Integrity Check:** A data verification check (`immich-external-backup-check.service`, Sundays at 08:00) validates pack integrity across repository subsets.

---

## Storage & Retention Contract

- **Filesystem:** ext4
- **UUID:** `00d3f54f-28f8-454c-929a-8c8191ab3624`
- **Mountpoint:** `/mnt/backups`
- **Repository Path:** `/mnt/backups/restic-repo`
- **Schedule:** Nightly at `03:30:00` (after Immich at 02:00, Home Assistant at 02:30, and Forgejo at 02:50).
- **Retention Policy:**
  - 3 latest snapshots (`--keep-last 3`)
  - 14 daily snapshots (`--keep-daily 14`)
  - 8 weekly snapshots (`--keep-weekly 8`)
  - 3 monthly snapshots (`--keep-monthly 3`)
- **Pruning Schedule:** Sundays at `06:00:00` (requires successful backup < 36h).
- **Integrity Check Schedule:** Sundays at `08:00:00` (`--read-data-subset=1/10`).

---

## Operating Procedures

### 1. Manual Backup Execution
To trigger an immediate backup outside the timer:
```sh
ssh adguard-netbird-01 'sudo systemctl start immich-external-backup.service'
ssh adguard-netbird-01 'sudo journalctl -u immich-external-backup.service -f'
```

### 2. Inspecting Snapshots
To list existing snapshots in the repository:
```sh
ssh adguard-netbird-01 '
export RESTIC_REPOSITORY="/mnt/backups/restic-repo"
export RESTIC_PASSWORD_FILE="/run/secrets/backup-restic-password"
sudo -E restic snapshots
'
```

### 3. Restoring Files

#### A. Restoring an Immich photo or entire album
```sh
ssh adguard-netbird-01 '
export RESTIC_REPOSITORY="/mnt/backups/restic-repo"
export RESTIC_PASSWORD_FILE="/run/secrets/backup-restic-password"
mkdir -p /tmp/restore-sample
# Restore the latest snapshot of photos
sudo -E restic restore latest --target /tmp/restore-sample --include "/run/backup-source/cluster/*/library"
ls -la /tmp/restore-sample
'
```

#### B. Restoring Immich PostgreSQL database
```sh
ssh adguard-netbird-01 '
export RESTIC_REPOSITORY="/mnt/backups/restic-repo"
export RESTIC_PASSWORD_FILE="/run/secrets/backup-restic-password"
mkdir -p /tmp/restore-db
sudo -E restic restore latest --target /tmp/restore-db --include "*.dump.gz"
ls -la /tmp/restore-db
'
```
The resulting `.dump.gz` file can then be restored using `pg_restore` per [postgres-disaster-recovery.md](postgres-disaster-recovery.md).

#### C. Restoring Forgejo repositories and database
```sh
ssh adguard-netbird-01 '
export RESTIC_REPOSITORY="/mnt/backups/restic-repo"
export RESTIC_PASSWORD_FILE="/run/secrets/backup-restic-password"
mkdir -p /tmp/restore-forgejo
sudo -E restic restore latest --target /tmp/restore-forgejo --include "*forgejo*"
ls -la /tmp/restore-forgejo
'
```

---

## Integrity & Hardware Health

1. **Repository Check:** Weekly integrity verification runs via `immich-external-backup-check.timer` (Sundays at 05:00 AM) using `restic check --read-data-subset=1/10`.
2. **SMART Monitoring:** `services.smartd` continuously monitors the 1 TB HDD (`/dev/disk/by-uuid/00d3f54f-28f8-454c-929a-8c8191ab3624`) and logs disk degradation or I/O errors to systemd journal.
3. **Prometheus Metrics:** The node exporter textfile collector at `/var/lib/node-exporter-textfile/immich_backup.prom` exports:
   - `immich_backup_last_status` (1 = success, 0 = failure)
   - `immich_backup_last_run_timestamp_seconds`
   - `immich_backup_last_success_timestamp_seconds`
