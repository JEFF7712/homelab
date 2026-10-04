# Laptop-to-NAS Syncthing Backup Implementation Plan

**Target System:** `laptop-nixos` (client) and `nas-01` (receiver)  
**Date:** 2026-10-03  
**Status:** Ready for Independent Audit (Deployment Held)  

---

## 1. Goal and Threat Model

**Goal:** Establish an automated, continuous, send-only backup pipeline syncing 12 personal, academic, and development trees from `laptop-nixos` to an encrypted ZFS dataset on `nas-01` in the homelab.

**Threat Model & Protection Guarantees:**
- **At-Rest Protection:** Data on `nas-01` is stored on an independent encrypted ZFS dataset (`tank/backups/syncthing`) with AES-256-GCM. Physical theft of NAS storage disks cannot reveal backed-up data without `/persist/keys/tank-syncthing.key`.
- **In-Transit Protection:** Mutual TLS authentication with pinned Device IDs over direct LAN or NetBird VPN (`10.0.30.20:22000`). Public relays (`relaysEnabled=false`), public announce servers (`globalAnnounceEnabled=false`), UPnP/NAT-PMP (`natEnabled=false`), usage reporting (`urAccepted=-1`), and crash reporting (`crashReportingEnabled=false`) are strictly disabled.
- **Client Mutation Protection:** Laptop folders are strictly `sendonly`. NAS file modifications or deletions can never propagate back to the laptop.
- **Ransomware / Accidental Deletion Protection:**
  - *Layer 1 (Syncthing Staggered Versioning):* Modified or deleted files on the laptop are automatically moved to `/tank/backups/syncthing/laptop/<folder>/.stversions/` on the NAS with a 30-day retention window (`cleanInterval=3600`, `maxAge=2592000`).
  - *Layer 2 (Native ZFS Snapshots):* `tank/backups/syncthing` is registered with Sanoid's `operational` template (30 daily automated snapshots, independent of Syncthing state). Provides retention-managed point-in-time recovery against client-side tampering, ransomware, or accidental deletion (does not provide WORM immutability against compromised NAS root).
- **Administration & Network Isolation:**
  - The Syncthing Web GUI on `nas-01` binds strictly to loopback `127.0.0.1:8384` with zero external firewall ports. Access requires an authenticated SSH tunnel.
  - Data transfer (22000 TCP/UDP) and local discovery (21027 UDP) are restricted via `networking.firewall.extraInputRules` to `{ 10.0.10.0/24, 10.0.30.0/24, 100.64.0.0/10 }`. NetBird traffic (`100.64.0.0/10`) reaches `nas-01` via transit routing through appliance `adguard-netbird-01` into `10.0.30.0/24`.

---

## 2. Device Identity & Pairing Matrix

Both nodes authenticate mutually using pre-generated cryptographic device IDs:

| Host | Hostname | Role | IP / Address | Syncthing Device ID |
|---|---|---|---|---|
| **Laptop** | `laptop-nixos` | Client / Send-Only | `dynamic` | `4LT3RLW-PVXTJAA-LBRBUSK-JDAEAXN-JH735IR-FDVT54T-Z4MQ6YH-JAVXZQU` |
| **NAS** | `nas-01` | Receiver / Receive-Only | `10.0.30.20:22000` | `JA3XY2G-IRMRCXV-ZD2UTD5-KXKSCBS-B32V4M6-Y5XBNAZ-VKZBLDF-SYRUQQB` |

Credentials storage:
- `laptop-nixos`: `/home/rupan/.config/syncthing/{cert.pem,key.pem,config.xml}` (persisted on `@home` btrfs subvolume).
- `nas-01`: `/persist/syncthing/{cert.pem,key.pem,config.xml}` (persisted on `zroot/persist` ZFS dataset).

---

## 3. Storage, Dataset & Network Specification

### OpenZFS Child Dataset on `nas-01`
- **Dataset:** `tank/backups/syncthing`
- **Mountpoint:** `/tank/backups/syncthing` (`options.mountpoint = "legacy"`)
- **Encryption:** `aes-256-gcm`
- **Key Format:** `passphrase` (256-bit base64 random secret)
- **Key Location:** `file:///persist/keys/tank-syncthing.key` (mode `0600 root:root`)
- **Disko Declaration:** Added to `flake/hosts/nas-01/tank-config.nix`.
- **Systemd Mount Unit:** `tank-backups-syncthing.mount`.
- **Service Dependency:** `systemd.services.syncthing` has `after = [ "tank-backups-syncthing.mount" ]` and `requires = [ "tank-backups-syncthing.mount" ]`.
- **Snapshot Policy:** Registered in `flake/modules/nas-data.nix` under `services.sanoid.datasets."tank/backups/syncthing".useTemplate = [ "operational" ]`. In NixOS Sanoid module, `recursive` defaults to `false`, so `tank/backups` and `tank/backups/syncthing` operate independently without snapshot duplication.
- **NFS Boundary Security:** The parent export `/tank/backups` in `nas-data.nix` does NOT set `nohide` or `crossmnt`. Under Linux NFS semantics, child filesystem mountpoints appear as empty stubs; `tank/backups/syncthing` data is not exposed across the parent NFS export. *Residual risk:* The parent `/tank/backups` dataset remains NFS-writable by `10.0.30.0/24`, meaning an NFS client could consume pool capacity (quota/DoS), though it cannot read or overwrite Syncthing backup payload files.

---

## 4. Folder Synchronization Matrix

All 12 folders share identical string identifiers across both machines:

| Folder ID | Laptop Source Path | NAS Destination Path | Laptop Mode | NAS Mode | Build Filters Applied |
|---|---|---|---|---|---|
| `laptop-documents-personal` | `/home/rupan/documents` | `/tank/backups/syncthing/laptop/documents` | `sendonly` | `receiveonly` | No |
| `laptop-documents-apps` | `/home/rupan/Documents` | `/tank/backups/syncthing/laptop/Documents` | `sendonly` | `receiveonly` | No |
| `laptop-projects` | `/home/rupan/projects` | `/tank/backups/syncthing/laptop/projects` | `sendonly` | `receiveonly` | **Yes** |
| `laptop-code` | `/home/rupan/code` | `/tank/backups/syncthing/laptop/code` | `sendonly` | `receiveonly` | **Yes** |
| `laptop-school` | `/home/rupan/school` | `/tank/backups/syncthing/laptop/school` | `sendonly` | `receiveonly` | **Yes** |
| `laptop-businesses` | `/home/rupan/businesses` | `/tank/backups/syncthing/laptop/businesses` | `sendonly` | `receiveonly` | **Yes** |
| `laptop-research` | `/home/rupan/research` | `/tank/backups/syncthing/laptop/research` | `sendonly` | `receiveonly` | **Yes** |
| `laptop-homelab` | `/home/rupan/homelab` | `/tank/backups/syncthing/laptop/homelab` | `sendonly` | `receiveonly` | **Yes** |
| `laptop-nixos` | `/home/rupan/nixos` | `/tank/backups/syncthing/laptop/nixos` | `sendonly` | `receiveonly` | **Yes** |
| `laptop-obsidian` | `/home/rupan/obsidian` | `/tank/backups/syncthing/laptop/obsidian` | `sendonly` | `receiveonly` | No |
| `laptop-pictures` | `/home/rupan/Pictures` | `/tank/backups/syncthing/laptop/Pictures` | `sendonly` | `receiveonly` | No |
| `laptop-videos` | `/home/rupan/Videos` | `/tank/backups/syncthing/laptop/Videos` | `sendonly` | `receiveonly` | No |

**Universal Build Artifact Filters (`buildIgnores`):**  
`node_modules`, `target`, `.direnv`, `result`, `result-*`, `.venv`, `venv`, `__pycache__`, `*.pyc`, `.build`, `build`, `dist`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`.

---

## 5. Offline Verification Evidence

The implementation has been verified offline with zero live mutations:

1. **Unit Test Suite (`tests/test_nas_syncthing.py`)**:
   - `test_dataset_encryption_configured`: Asserts `aes-256-gcm` and key location.
   - `test_sanoid_snapshot_policy_configured`: Asserts `operational` template on `tank/backups/syncthing`.
   - `test_syncthing_mount_ordering`: Asserts dependency on `tank-backups-syncthing.mount`.
   - `test_gui_bound_to_loopback_and_not_in_firewall`: Asserts loopback GUI and no external port.
   - `test_syncthing_sync_firewall_ports`: Asserts sync and discovery ports are not open globally, and are restricted to `{ 10.0.10.0/24, 10.0.30.0/24, 100.64.0.0/10 }` via `extraInputRules`.
   - `test_telemetry_and_public_discovery_disabled`: Asserts strict private LAN options.
   - `test_all_folders_are_receive_only_with_staggered_versioning`: Asserts all 12 folders.
   - `test_laptop_device_pairing`: Asserts cryptographic device ID.
   - Combined test run with `test_nas_storage`: **15/15 passed** (`Ran 15 tests in 3.083s - OK`).
2. **Laptop Configuration Evaluation**:
   - `nix eval .#nixosConfigurations.laptop.config.services.syncthing.settings`: Evaluated all 12 `sendonly` folders, device addresses, and ignore patterns.
   - `just check-laptop-safety`: **Passed** (`true`).
3. **Static Analysis & Linting**:
   - `shellcheck -S error scripts/nas/verify-syncthing-recovery.sh`: **Passed**.
   - `nixfmt` and `ruff format`: All touched files clean and compliant.
4. **Agent Workflow Verification Records**:
   - Source-bound verification receipts stored in `.agent-state/` under task `syncthing-backup`.

---

## 6. Execution & Deployment Runbook

### Phase 1: Pre-Switch Dataset Provisioning on `nas-01`
*Prerequisite: Run wholly as root (`sudo bash -euo pipefail`). User `syncthing` does not exist prior to activation; dataset is mounted with root ownership.*

```bash
# Step 1.1: Privileged, Non-Clobbering Key Generation
ssh rupan@10.0.30.20 'sudo bash -euo pipefail' << 'EOF'
  install -d -m 0700 /persist/keys
  KEY="/persist/keys/tank-syncthing.key"
  if [ -e "$KEY" ]; then
    echo "[INFO] Key file $KEY already exists."
    if [ ! -f "$KEY" ] || [ ! -s "$KEY" ]; then
      echo "[ERROR] Key $KEY exists but is not a non-empty regular file!" >&2
      exit 1
    fi
    chmod 0600 "$KEY"
    echo "[OK] Existing key verified (0600)."
  else
    echo "[INFO] Creating new key $KEY with atomic noclobber..."
    (
      set -C
      umask 077
      head -c 32 /dev/urandom | base64 > "$KEY"
    )
    chmod 0600 "$KEY"
    echo "[OK] Key created with mode 0600."
  fi
EOF

# Step 1.2: ZFS Dataset Creation, Property Validation, Dry-Run Key Verification, and Initial Mount
ssh rupan@10.0.30.20 'sudo bash -euo pipefail' << 'EOF'
  DATASET="tank/backups/syncthing"
  KEYFILE="/persist/keys/tank-syncthing.key"
  MOUNTPOINT="/tank/backups/syncthing"

  if ! zfs list -H -o name "$DATASET" >/dev/null 2>&1; then
    echo "[INFO] Creating encrypted dataset $DATASET..."
    zfs create \
      -o encryption=aes-256-gcm \
      -o keyformat=passphrase \
      -o keylocation="file://$KEYFILE" \
      -o mountpoint=legacy \
      "$DATASET"
  fi

  echo "[INFO] Validating dataset properties..."
  [ "$(zfs get -H -o value encryption "$DATASET")" = "aes-256-gcm" ] || { echo "Encryption mismatch" >&2; exit 1; }
  [ "$(zfs get -H -o value encryptionroot "$DATASET")" = "$DATASET" ] || { echo "Encryptionroot mismatch" >&2; exit 1; }
  [ "$(zfs get -H -o value keyformat "$DATASET")" = "passphrase" ] || { echo "Keyformat mismatch" >&2; exit 1; }
  [ "$(zfs get -H -o value keylocation "$DATASET")" = "file://$KEYFILE" ] || { echo "Keylocation mismatch" >&2; exit 1; }

  echo "[INFO] Verifying saved key unlock via zfs load-key -n..."
  zfs load-key -n "$DATASET"

  KEYSTATUS=$(zfs get -H -o value keystatus "$DATASET")
  if [ "$KEYSTATUS" = "unavailable" ]; then
    zfs load-key "$DATASET"
  fi

  install -d -m 0750 -o root -g root "$MOUNTPOINT"
  if ! findmnt "$MOUNTPOINT" >/dev/null 2>&1; then
    mount -t zfs "$DATASET" "$MOUNTPOINT"
  fi

  MOUNT_SRC=$(findmnt -n -o SOURCE "$MOUNTPOINT")
  [ "$MOUNT_SRC" = "$DATASET" ] || { echo "Mount source mismatch: $MOUNT_SRC" >&2; exit 1; }
  echo "[OK] $DATASET verified and mounted at $MOUNTPOINT."
EOF
```

### Phase 2: Host Rebuild & System Switching
*Prerequisite: Review concurrent working tree changes in `homelab` prior to building.*

```bash
# Step 2.1: Rebuild and switch nas-01
nixos-rebuild switch \
  --flake ./flake#nas-01 \
  --target-host rupan@10.0.30.20 \
  --build-host rupan@10.0.30.20 \
  --elevate=sudo

# Step 2.2: Rebuild and switch laptop-nixos
sudo nixos-rebuild switch --flake /home/rupan/nixos#laptop
```

### Phase 3: Live Verification & Acceptance Test
Run the committed acceptance script [`scripts/nas/verify-syncthing-recovery.sh`](file:///home/rupan/homelab/scripts/nas/verify-syncthing-recovery.sh):

```bash
bash scripts/nas/verify-syncthing-recovery.sh
```

**Acceptance Preconditions:**
- **Execution Host:** Must be executed on `laptop-nixos` as user `rupan`.
- **Filesystem Watcher:** Inotify watcher must be active on the laptop so changes to `$HOME/documents` trigger instantaneous synchronization (`rescanIntervalS = 3600`).
- **Remote Host:** `nas-01` reachable at `10.0.30.20` via SSH with sudo access.

**Automated Acceptance Protocol Executed by Script:**
1. **API Connection Check**: Authenticates with `X-API-Key` and confirms laptop device connection.
2. **Path Mapping Validation**: Verifies via `GET /rest/config/folders` that all 12 folders are declared as `receiveonly` and map to their exact paths under `/tank/backups/syncthing/laptop/`.
3. **Completion Gate**: Polls `GET /rest/db/status?folder=<id>` across all 12 folders in a loop (timeout: 300s, poll interval: 5s), waiting for steady state (`state: "idle"`, `needTotalItems: 0`, `needBytes: 0`, and `pullErrors: 0`).
4. **Data Integrity Gate**: Creates a random probe file on the laptop, awaits transfer, and asserts identical SHA256 checksums on both hosts.
5. **Staggered Versioning Recovery**: Takes a manual ZFS snapshot, modifies the probe file on the laptop, verifies the original version is moved to `.stversions/${stem}~*.txt`, copies it into an isolated temporary directory, and proves SHA256 equality.
6. **Deletion Archiving Proof**: Deletes the probe file on the laptop, verifies deletion syncs, and confirms the modified version is moved to `.stversions`.
7. **Native ZFS Snapshot Recovery**: Copies the original file directly out of `/tank/backups/syncthing/.zfs/snapshot/<snap>/laptop/documents/...` into an isolated restore directory and proves SHA256 equality.
8. **Receive-Only Folder Revert**: Calls `POST /rest/db/revert?folder=laptop-documents-personal` to ensure no lingering local addition state remains on the NAS.
9. **Privileged Cleanup**: Automatically cleans up probe files, version archives, restore directories, and the test snapshot via an exit trap.

---

## 7. Rollback and Recovery Strategy

If any deployment step or acceptance test fails:

1. **Service Rollback:**
   - Laptop: `sudo nixos-rebuild switch --rollback`
   - NAS: `nixos-rebuild switch --rollback --target-host rupan@10.0.30.20 --elevate=sudo`
2. **Dataset Isolation:**
   - The encrypted dataset `tank/backups/syncthing` is mounted with `options = [ "nofail" ]`. If it cannot be unlocked or mounted, the rest of `nas-01` continues booting normally without degradation to other storage services (`photos`, `documents`, `attic`, `forgejo`).
3. **Disaster Recovery Key:**
   - The passphrase generated at `/persist/keys/tank-syncthing.key` can be backed up to offline password manager / recovery keys. The dataset can be imported and mounted on any OpenZFS system using:
     ```sh
     zpool import tank
     zfs load-key -L prompt tank/backups/syncthing
     zfs mount tank/backups/syncthing
     ```
