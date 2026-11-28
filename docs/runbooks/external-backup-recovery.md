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
1. **Pull Architecture:** `adguard-netbird-01` initiates the connection. `nas-01`, `homelab-01`, and `homelab-04` have no SSH keys or write credentials to `adguard-netbird-01`, so a compromise on any source node cannot delete or tamper with backups.
2. **Server-Side Forced Read-Only SFTP:** Source hosts restrict the `backup-reader` user via OpenSSH `ForceCommand internal-sftp -R` with all port and agent forwarding disabled.
3. **Fail-Closed Safeguards:** The backup service requires `/mnt/backups` to be an active mountpoint with a verified canary (`.backup-target-canary`) and at least 15 GiB free space before running. Missing mounts or sources immediately abort to prevent writing to the 2GB root tmpfs.
4. **Repository Isolation & Least Privilege:** `/mnt/backups` and `/mnt/backups/restic-repo` are strictly restricted to mode `0700 root:root` enforced via tmpfiles rules and service startup. Ordinary daemon accounts (AdGuard, NetBird, Mosquitto, Zigbee2MQTT) cannot traverse or inspect the backup repository.
5. **Database Consistency & Point-in-Time ZFS Snapshots:**
   - **Application PVCs (CouchDB, SQLite, Immich):** Backed up directly from a dedicated frozen point-in-time ZFS snapshot (`tank/cluster@backup-nightly` mounted onto `/run/backup-source/nas/cluster`). This eliminates file skew, torn pages, and WAL log mismatches during live operations.
   - **CouchDB:** Crash-consistent at the filesystem snapshot level due to its append-only B-tree structure.
   - **Arr Applications (Sonarr, Radarr, Prowlarr, Bookshelf):** Backed up via the frozen ZFS snapshot and supplemented by internal native application scheduled backups (`Backups/scheduled/*.zip`).
   - **Woodpecker & Forgejo:** Created using online SQLite `.backup` APIs before files are pulled.
6. **Cycle Verification Protocol (No Yesterday's Dumps):** Every source preparation job produces an atomic `backup-manifest.json` (k3s publishes it inside a frozen generation selected by `LATEST`) recording `status: "success"`, the current `cycle: "YYYYMMDD"`, dump file name, and file size. The backup runner strictly verifies that today's manifest matches the current cycle and was generated within the last 6 hours, failing closed if any source dump failed or is stale.
7. **Timing Coordination:** The runner actively coordinates with `k3s-backup-prep` on `homelab-01`, polling for up to 5 minutes to confirm completion of the current cycle's etcd snapshot before Restic execution begins.
8. **Separate Pruning & Mutual Exclusion:** Pruning runs in an independent service (`immich-external-backup-prune.service`, Sundays at 06:00) that strictly refuses to execute if a backup or check is active, if the mutual exclusion lock is held, or if the last successful backup is older than 36 hours. Automatic pruning enforces `--keep-last 3` alongside the 14-daily, 8-weekly, and 3-monthly policy.
9. **Weekly Integrity Check:** A data verification check (`immich-external-backup-check.service`, Sundays at 08:00) validates pack integrity across repository subsets.

---

## Storage & Retention Contract

- **Filesystem:** ext4
- **UUID:** `00d3f54f-28f8-454c-929a-8c8191ab3624`
- **Mountpoint:** `/mnt/backups`
- **Repository Path:** `/mnt/backups/restic-repo`
- **Schedule:** Nightly at `03:30:00` (after Immich at 02:00, Home Assistant at 02:30, and Forgejo at 02:50).
- **Syncthing folders:** `/tank/backups/syncthing` is pulled live (not from a snapshot) after the critical snapshot succeeds, as Restic host `adguard-netbird-01-syncthing` (its own prune group), time-boxed to the unit timeout. An interrupted run keeps its uploaded data and resumes the next night; `syncthing_backup_last_success_timestamp_seconds` records completion. `backup-reader` reads the folders through the `syncthing` group, and the run fails closed if they are not listable.
- **Laptop restic repository:** `/tank/backups/restic` (the restic REST server in `nas-restic.nix`) is pulled after the critical snapshot and before Syncthing, as Restic host `adguard-netbird-01-laptop` (its own prune group), time-boxed the same way. Packs are immutable, so each night copies only the laptop's new ones. `backup-reader` reads it through the `restic` group (`--group-accessible-repos`); the run fails closed if the repository is not listable, and `laptop_restic_backup_last_success_timestamp_seconds` records completion. Restoring it gives back the repository, which still needs the laptop's restic password (`restic.password` in nixos-config's SOPS file).
- **Registry:** not pulled off-host (zot keeps it `0700`); it is rebuildable from Forgejo sources and upstream mirrors, and has a local copy on nas-01 (see below).
- **Local copy on nas-01:** `nas-backup-local.service` keeps a second, same-host Restic copy of tank's non-media data (`nas-restic`) and the registry (`registry-restic`) on `/mnt/backup-local` (1 TB WD RE4). It is the fast restore path; this external disk is the off-host one.
- **Retention Policy:**
  - 3 latest snapshots (`--keep-last 3`)
  - 14 daily snapshots (`--keep-daily 14`)
  - 8 weekly snapshots (`--keep-weekly 8`)
  - 3 monthly snapshots (`--keep-monthly 3`)
- **Pruning Schedule:** Sundays at `06:00:00` (requires successful backup < 36h).
- **Integrity Check Schedule:** Sundays at `08:00:00` (`--read-data-subset=1/10`).

---

## Operating Procedures

If source validation reports that the laptop Restic repository is unreadable,
check `stat -c '%a %U:%G' /tank/backups/restic` on `nas-01`. NixOS activation
manages that directory as the `restic` user's home and should leave it at
`750 restic:restic`. The `backup-reader` account needs the `restic` group to
traverse it over read-only SFTP. Verify recovery during the next nightly cycle
with fresh source manifests.

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

#### C. Restoring Forgejo repositories, database, and state
```sh
ssh adguard-netbird-01 '
export RESTIC_REPOSITORY="/mnt/backups/restic-repo"
export RESTIC_PASSWORD_FILE="/run/secrets/backup-restic-password"
mkdir -p /tmp/restore-forgejo
sudo -E restic restore latest --target /tmp/restore-forgejo --include "*forgejo*"
ls -la /tmp/restore-forgejo
'
```
The restored files include:
- Repositories under `.../forgejo/repositories`
- Consistent SQLite database dumps under `.../forgejo/backups/forgejo-*.sqlite`
- Attachments, avatars, JWT secrets, and SSH keys under `.../forgejo/backups/state`

#### D. Restoring k3s etcd snapshot & server token
```sh
ssh adguard-netbird-01 '
export RESTIC_REPOSITORY="/mnt/backups/restic-repo"
export RESTIC_PASSWORD_FILE="/run/secrets/backup-restic-password"
mkdir -p /tmp/restore-k3s
sudo -E restic restore latest --target /tmp/restore-k3s --include "*k3s*"
ls -la /tmp/restore-k3s
'
```
Each k3s export is an immutable directory under `generations/`, selected once
by the `LATEST` pointer before the backup starts. Its `backup-manifest.json`
binds a newly created etcd snapshot, its matching server token, encryption
configuration when present, and the existing SSH host-key recovery member. The
prep job refuses token/key changes during capture and validates the snapshot.
The pull job preserves current-cycle and six-hour freshness checks and reads
back every critical member from the independent Restic snapshot before recording
success. The read-back streams only hashes into memory; no credentials appear in
logs. A nonfatal Restic result for unrelated cache churn cannot hide a missing
recovery member. Scheduled etcd snapshot permissions are unchanged.

Restore all members of **one** generation together. Never pair a snapshot with
an arbitrary newer token or encryption file. Use a root-only recovery directory
on tmpfs for these credential-bearing artifacts. Before starting a replacement
server, restore `server-token` to `/var/lib/rancher/k3s/server/token`, and restore
`encryption-config.json`, when present, to
`/var/lib/rancher/k3s/server/cred/encryption-config.json`. Use the pinned K3s
version and saved token for `server --cluster-reset
--cluster-reset-restore-path=<etcd-snapshot>` in the isolated recovery environment.
Do not reset a production member for a drill.

The source helper supports `verify --generation <restored-generation>
--restore-runtime <private-tmpfs-directory>`. It verifies all member hashes and
exercises offline `etcdutl snapshot restore` without starting a network member or
changing the live K3s data directory. The runtime needs at least three times the
snapshot size plus 64 MiB free. Success proves the artifact is structurally
restorable; it does not establish independent decryption-key custody or a fully
running restored cluster.

Deploy the producer on homelab-01 and confirm a new `LATEST` generation before
activating the pull runner on adguard-netbird-01. Prior generations and old loose
files remain intact; no migration cleanup or generation pruning is performed.
The old runner can still read its last published loose set during staged rollout,
but that old set will eventually fail freshness checks until the new runner is
activated. After both hosts are updated, run preparation then backup and require
the final independent-copy verification before accepting the cycle.

#### E. Restoring Woodpecker CI
```sh
ssh adguard-netbird-01 '
export RESTIC_REPOSITORY="/mnt/backups/restic-repo"
export RESTIC_PASSWORD_FILE="/run/secrets/backup-restic-password"
mkdir -p /tmp/restore-woodpecker
sudo -E restic restore latest --target /tmp/restore-woodpecker --include "*woodpecker*"
ls -la /tmp/restore-woodpecker
'
```
Restore the SQLite database into `/persist/woodpecker/server-data/woodpecker.sqlite` and signature keys with `woodpecker-server` stopped.

#### F. Restoring OpenTofu State & Garage S3
```sh
ssh adguard-netbird-01 '
export RESTIC_REPOSITORY="/mnt/backups/restic-repo"
export RESTIC_PASSWORD_FILE="/run/secrets/backup-restic-password"
mkdir -p /tmp/restore-platform
sudo -E restic restore latest --target /tmp/restore-platform --include "*platform-state*" --include "*/s3*"
ls -la /tmp/restore-platform
'
```
Contains `state.sqlite` for OpenTofu and Garage object data.

#### G. Restoring Application State (Sonarr, Radarr, Prowlarr, Calibre, Navidrome, CouchDB, Bookshelf)
```sh
ssh adguard-netbird-01 '
export RESTIC_REPOSITORY="/mnt/backups/restic-repo"
export RESTIC_PASSWORD_FILE="/run/secrets/backup-restic-password"
mkdir -p /tmp/restore-apps
sudo -E restic restore latest --target /tmp/restore-apps --include "*media-*" --include "*obsidian-*"
ls -la /tmp/restore-apps
'
```

#### H. Restoring Local Appliance State (AdGuard Home, NetBird, Mosquitto, Zigbee2MQTT)
```sh
ssh adguard-netbird-01 '
export RESTIC_REPOSITORY="/mnt/backups/restic-repo"
export RESTIC_PASSWORD_FILE="/run/secrets/backup-restic-password"
mkdir -p /tmp/restore-local
sudo -E restic restore latest --target /tmp/restore-local --include "/var/lib/*"
ls -la /tmp/restore-local
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

---

## Offsite Copy (home-pc-01)

`immich-offsite-copy.service` (weekly, Mondays 09:30) copies this repository with `restic copy` to an append-only `rest-server` on `home-pc-01`, the family desktop at another house (`flake/hosts/home-pc-01/offsite-backup.nix`, [home-pc-01](home-pc-01.md)). The destination was initialised from this repository with `--copy-chunker-params`, so it deduplicates the same way and uses **the same repository password**. Append-only means a compromised homelab can add snapshots but cannot delete or rewrite them; nothing prunes the offsite copy. The data stays encrypted, so the desktop never sees plaintext.

- **Path:** `adguard-netbird-01` connects to `rest:http://100.94.53.129:8000/homelab/` over NetBird. The server listens only on `wt0`, and the NetBird policy `homelab-to-home-pc-01` (TCP 22 and 8000) admits `adguard-netbird-01` alone. On the PC the repository is `/home/.offsite-backup/homelab`, on the 622 GB `/home` partition; the server refuses to start if that partition is missing.
- **Timing:** it runs after the Sunday prune and check, reads under a shared restic lock beside the nightly backups, and stops itself by Sunday 05:30 so the next prune never meets it. That allows close to six days, enough for the first copy (about 116 GB) over a home uplink; an interrupted copy keeps its uploaded packs and resumes the next week.
- **Alerts:** `offsite_backup_last_status` and `offsite_backup_last_success_timestamp_seconds` in `offsite_backup.prom` drive `OffsiteBackupStale` (warning after 9 days: a missed week plus a desktop left off) and `OffsiteBackupMissing` (warning if no copy has ever completed within 10 days).

Restore from the offsite copy with the same password as this repository (see below), from any machine that can reach the PC:

```sh
export RESTIC_REPOSITORY=rest:http://100.94.53.129:8000/homelab/
export RESTIC_PASSWORD_FILE=/path/to/backup-restic-password
restic snapshots
restic restore latest --tag immich --target /restore
```

With the homelab gone, copy `/home/.offsite-backup/homelab` off the PC (or run `restic -r /home/.offsite-backup/homelab ...` on it as root) and restore the same way.

---

## Sensitive Key Protection & Zero-Dependency Recovery

The backup contains high-value recovery materials (host SSH private keys, ZFS pool encryption keys, k3s cluster tokens, and Woodpecker signing keys). These are protected with strict defense-in-depth:

### 1. In-Transit and At-Rest Permissions
- The repository disk at `/mnt/backups` and the Restic repository `/mnt/backups/restic-repo` are mode `0700 root:root`.
- Non-root processes on `adguard-netbird-01` cannot enter or read `/mnt/backups`.
- Source staging directories (`/tank/backups/platform-state`, `/persist/backup-source/k3s`, `/persist/woodpecker/backups`) are mode `0750` with group `backup-reader` and `internal-sftp -R` enforcement.

### 2. Post-Restoration Permission Enforcement
When restoring secrets (SSH keys, ZFS keys, tokens), enforce restrictive permissions before starting services:
```sh
# Host keys and ZFS keys
chmod 0600 /persist/etc/ssh/ssh_host_*_key
chmod 0600 /persist/keys/*
chmod 0600 /var/lib/rancher/k3s/server/token
chown -R root:root /persist/keys /persist/etc/ssh
```

### 3. Independent Recovery (Zero-Homelab Dependency)
To prevent a circular dependency where credentials needed to restore are trapped inside the encrypted repository:
- The Restic repository password is encrypted in Git under [`secrets/hosts/adguard-netbird-01.sops.yaml`](file:///home/rupan/homelab/secrets/hosts/adguard-netbird-01.sops.yaml) using the operator's SOPS Age master key.
- The operator can decrypt this key on an air-gapped machine using their Age recovery key (`SOPS_AGE_KEY` or `~/.config/sops/age/keys.txt`):
  ```sh
  sops -d --extract '["backup-restic-password"]' secrets/hosts/adguard-netbird-01.sops.yaml
  ```
- Alternatively, use the automated offline recovery kit generator:
  ```sh
  ./scripts/backups/export-offline-recovery-kit.sh /path/to/offline-usb
  ```
  This exports a standalone recovery bundle containing `emergency-restore.sh`, the decrypted repository password, and step-by-step restoration instructions ready for bare-metal rebuilds.


## Independent cold-recovery acceptance

Full independent platform recovery remains unproven until the existing gates in
[the platform recovery contract](sovereign-platform-recovery.md) pass against an
independently held checkpoint and key custody. The known checkpoint paths are
not present in this audit workspace; the retained recovery-guest procedure needs
a suitably isolated host with capacity for a 6 GiB VM. Do not run it inside the
shared 8 GiB agent workspace or count live-cluster credentials as independent
recovery evidence. The existing offline kit exporter can prepare operator-held
media, but a kit export alone is not a restore receipt. Offsite backup expansion
and physical display repair remain deferred for this audit task.
