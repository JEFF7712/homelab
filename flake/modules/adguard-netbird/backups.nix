{
  config,
  pkgs,
  lib,
  ...
}:
let
  backupScript = pkgs.writeShellScript "run-immich-external-backup" ''
        set -euo pipefail

        TEXTFILE_DIR="/var/lib/node-exporter-textfile"
        PROM_FILE="$TEXTFILE_DIR/immich_backup.prom"
        TMP_PROM="$PROM_FILE.tmp"

        record_metric() {
          local status="$1"
          local timestamp="$2"
          if [ -z "$timestamp" ] && [ -f "$PROM_FILE" ]; then
            timestamp=$(${pkgs.gnugrep}/bin/grep "^immich_backup_last_success_timestamp_seconds " "$PROM_FILE" | ${pkgs.gawk}/bin/awk '{print $2}' || true)
          fi
          ${pkgs.coreutils}/bin/mkdir -p "$TEXTFILE_DIR"
          cat << EOF > "$TMP_PROM"
    # HELP immich_backup_last_status Exit status of immich backup (1=success, 0=failure)
    # TYPE immich_backup_last_status gauge
    immich_backup_last_status $status
    # HELP immich_backup_last_run_timestamp_seconds Timestamp of last backup execution attempt
    # TYPE immich_backup_last_run_timestamp_seconds gauge
    immich_backup_last_run_timestamp_seconds $(date +%s)
    EOF
          if [ -n "$timestamp" ]; then
            cat << EOF >> "$TMP_PROM"
    # HELP immich_backup_last_success_timestamp_seconds Timestamp of last successful backup completion
    # TYPE immich_backup_last_success_timestamp_seconds gauge
    immich_backup_last_success_timestamp_seconds $timestamp
    EOF
          fi
          ${pkgs.coreutils}/bin/mv -f "$TMP_PROM" "$PROM_FILE"
        }

        fail() {
          echo "FAIL-CLOSED: $*" >&2
          record_metric 0 ""
          exit 1
        }

        exec 200>"/run/lock/immich-external-ops.lock"
        if ! ${pkgs.util-linux}/bin/flock -n 200; then
          fail "Another external backup operation (backup, prune, or check) is currently running"
        fi

        is_unit_running() {
          local state
          state=$(${pkgs.systemd}/bin/systemctl show -p ActiveState --value "$1" 2>/dev/null || true)
          [ "$state" = "active" ] || [ "$state" = "activating" ]
        }

        if is_unit_running immich-external-backup-prune.service; then
          fail "Pruning service is currently active; aborting backup"
        fi
        if is_unit_running immich-external-backup-check.service; then
          fail "Integrity check service is currently active; aborting backup"
        fi

        echo "==> [1/5] Verifying backup destination /mnt/backups..."
        if ! ${pkgs.util-linux}/bin/mountpoint -q /mnt/backups; then
          fail "/mnt/backups is not a mounted filesystem"
        fi

        # Restrict permissions so no unprivileged user or daemon can access the backup disk
        ${pkgs.coreutils}/bin/chmod 0700 /mnt/backups
        if [ -d /mnt/backups/restic-repo ]; then
          ${pkgs.coreutils}/bin/chmod 0700 /mnt/backups/restic-repo
        fi

        if [ ! -f /mnt/backups/.backup-target-canary ]; then
          fail "Canary file /mnt/backups/.backup-target-canary is missing"
        fi

        FREE_KB=$(${pkgs.coreutils}/bin/df -k --output=avail /mnt/backups | ${pkgs.gawk}/bin/awk 'NR==2 {print $1}')
        if [ "$FREE_KB" -lt 15728640 ]; then
          fail "Insufficient free space on /mnt/backups (< 15 GiB free: $FREE_KB KB)"
        fi

        echo "==> [2/5] Mounting remote sources via read-only SSHFS..."
        BASE_MOUNT_DIR="/run/backup-source"
        NAS_MOUNT="$BASE_MOUNT_DIR/nas"
        NAS_CLUSTER_MOUNT="$NAS_MOUNT/cluster"
        K3S_MOUNT="$BASE_MOUNT_DIR/k3s"
        WOODPECKER_MOUNT="$BASE_MOUNT_DIR/woodpecker"

        for mnt in "$NAS_CLUSTER_MOUNT" "$NAS_MOUNT" "$K3S_MOUNT" "$WOODPECKER_MOUNT"; do
          if ${pkgs.util-linux}/bin/mountpoint -q "$mnt" 2>/dev/null; then
            ${pkgs.fuse}/bin/fusermount -u "$mnt" 2>/dev/null || ${pkgs.util-linux}/bin/umount -l "$mnt" 2>/dev/null || true
          fi
          if [ -d "$mnt" ]; then
            ${pkgs.coreutils}/bin/rmdir "$mnt" 2>/dev/null || true
          fi
        done
        ${pkgs.coreutils}/bin/mkdir -p "$NAS_MOUNT" "$K3S_MOUNT" "$WOODPECKER_MOUNT"

        cleanup() {
          local exit_code=$?
          echo "Cleaning up remote mounts..."
          for mnt in "$NAS_CLUSTER_MOUNT" "$NAS_MOUNT" "$K3S_MOUNT" "$WOODPECKER_MOUNT"; do
            if ${pkgs.util-linux}/bin/mountpoint -q "$mnt" 2>/dev/null; then
              ${pkgs.fuse}/bin/fusermount -u "$mnt" 2>/dev/null || ${pkgs.util-linux}/bin/umount -l "$mnt" 2>/dev/null || true
            fi
            if [ -d "$mnt" ]; then
              ${pkgs.coreutils}/bin/rmdir "$mnt" 2>/dev/null || true
            fi
          done
          ${pkgs.coreutils}/bin/rmdir "$BASE_MOUNT_DIR" 2>/dev/null || true
          return $exit_code
        }
        trap cleanup EXIT

        SSH_KEY="''${CREDENTIALS_DIRECTORY}/ssh_key"
        if [ ! -f "$SSH_KEY" ]; then
          SSH_KEY="${config.sops.secrets.backup-reader-ssh-key.path}"
        fi

        # Mount 1: nas-01 (/tank)
        ${pkgs.sshfs}/bin/sshfs backup-reader@10.0.30.20:/tank "$NAS_MOUNT" \
          -o ro,allow_other,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3 \
          -o cache=yes,kernel_cache,auto_cache \
          -o IdentityFile="$SSH_KEY" \
          -o StrictHostKeyChecking=accept-new

        # Mount 2: nas-01 frozen ZFS snapshot (/tank/cluster/.zfs/snapshot/backup-nightly onto $NAS_CLUSTER_MOUNT)
        # This guarantees 100% transactional point-in-time consistency for all application PVCs (CouchDB, SQLite, Immich)
        ${pkgs.coreutils}/bin/mkdir -p "$NAS_CLUSTER_MOUNT"
        ${pkgs.sshfs}/bin/sshfs backup-reader@10.0.30.20:/tank/cluster/.zfs/snapshot/backup-nightly "$NAS_CLUSTER_MOUNT" \
          -o ro,allow_other,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3 \
          -o cache=yes,kernel_cache,auto_cache \
          -o IdentityFile="$SSH_KEY" \
          -o StrictHostKeyChecking=accept-new

        # Mount 3: homelab-01 (/persist/backup-source/k3s)
        ${pkgs.sshfs}/bin/sshfs backup-reader@10.0.30.11:/persist/backup-source/k3s "$K3S_MOUNT" \
          -o ro,allow_other,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3 \
          -o cache=yes,kernel_cache,auto_cache \
          -o IdentityFile="$SSH_KEY" \
          -o StrictHostKeyChecking=accept-new

        # Mount 4: homelab-04 (/persist/woodpecker/backups)
        ${pkgs.sshfs}/bin/sshfs backup-reader@10.0.30.14:/persist/woodpecker/backups "$WOODPECKER_MOUNT" \
          -o ro,allow_other,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3 \
          -o cache=yes,kernel_cache,auto_cache \
          -o IdentityFile="$SSH_KEY" \
          -o StrictHostKeyChecking=accept-new

        echo "==> [3/5] Validating source paths and content freshness for current cycle..."
        CURRENT_CYCLE=$(${pkgs.coreutils}/bin/date +%Y%m%d)
        NOW_EPOCH=$(${pkgs.coreutils}/bin/date +%s)
        MAX_MANIFEST_AGE=21600 # 6 hours max age

        # Coordination: Ensure k3s preparation has completed for current cycle (poll up to 300s)
        K3S_MANIFEST="$K3S_MOUNT/backup-manifest.json"
        WAIT_SECONDS=0
        while [ "$WAIT_SECONDS" -lt 300 ]; do
          if [ -s "$K3S_MANIFEST" ]; then
            K3S_CYCLE=$(${pkgs.jq}/bin/jq -r '.cycle // empty' "$K3S_MANIFEST" 2>/dev/null || true)
            K3S_STATUS=$(${pkgs.jq}/bin/jq -r '.status // empty' "$K3S_MANIFEST" 2>/dev/null || true)
            if [ "$K3S_CYCLE" = "$CURRENT_CYCLE" ] && [ "$K3S_STATUS" = "success" ]; then
              echo "k3s-backup-prep cycle $CURRENT_CYCLE confirmed ready."
              break
            fi
          fi
          echo "Waiting for k3s-backup-prep cycle $CURRENT_CYCLE to complete... ($WAIT_SECONDS/300s)"
          ${pkgs.coreutils}/bin/sleep 15
          WAIT_SECONDS=$((WAIT_SECONDS + 15))
        done
        if [ "$WAIT_SECONDS" -ge 300 ]; then
          fail "Timed out waiting for k3s-backup-prep to complete successfully for cycle $CURRENT_CYCLE"
        fi

        # 1. Woodpecker manifest verification
        WP_MANIFEST="$WOODPECKER_MOUNT/backup-manifest.json"
        if [ ! -s "$WP_MANIFEST" ]; then
          fail "Woodpecker backup manifest missing or empty at $WP_MANIFEST"
        fi
        WP_STATUS=$(${pkgs.jq}/bin/jq -r '.status // empty' "$WP_MANIFEST")
        WP_CYCLE=$(${pkgs.jq}/bin/jq -r '.cycle // empty' "$WP_MANIFEST")
        WP_TS=$(${pkgs.jq}/bin/jq -r '.timestamp // 0' "$WP_MANIFEST")
        WP_DB_FILE=$(${pkgs.jq}/bin/jq -r '.database_file // empty' "$WP_MANIFEST")
        WP_DB_BYTES=$(${pkgs.jq}/bin/jq -r '.database_bytes // 0' "$WP_MANIFEST")

        if [ "$WP_STATUS" != "success" ]; then
          fail "Woodpecker dump manifest reported failure status: $WP_STATUS"
        fi
        if [ "$WP_CYCLE" != "$CURRENT_CYCLE" ]; then
          fail "Woodpecker dump is stale: manifest cycle $WP_CYCLE does not match current cycle $CURRENT_CYCLE"
        fi
        if [ "$((NOW_EPOCH - WP_TS))" -gt "$MAX_MANIFEST_AGE" ]; then
          fail "Woodpecker dump manifest timestamp too old ($((NOW_EPOCH - WP_TS))s > $MAX_MANIFEST_AGE s)"
        fi
        if [ ! -s "$WOODPECKER_MOUNT/$WP_DB_FILE" ]; then
          fail "Woodpecker database file $WP_DB_FILE missing or empty"
        fi
        if [ "$WP_DB_BYTES" -lt 104857600 ]; then
          fail "Woodpecker database suspiciously small ($WP_DB_BYTES bytes < 100MB)"
        fi
        echo "Validated Woodpecker cycle $CURRENT_CYCLE dump: $WP_DB_FILE ($(($WP_DB_BYTES / 1024 / 1024)) MB)"

        # 2. Forgejo manifest verification
        FJ_MANIFEST="$NAS_MOUNT/forgejo/backups/backup-manifest.json"
        if [ ! -s "$FJ_MANIFEST" ]; then
          fail "Forgejo backup manifest missing or empty at $FJ_MANIFEST"
        fi
        FJ_STATUS=$(${pkgs.jq}/bin/jq -r '.status // empty' "$FJ_MANIFEST")
        FJ_CYCLE=$(${pkgs.jq}/bin/jq -r '.cycle // empty' "$FJ_MANIFEST")
        FJ_TS=$(${pkgs.jq}/bin/jq -r '.timestamp // 0' "$FJ_MANIFEST")
        FJ_DB_FILE=$(${pkgs.jq}/bin/jq -r '.database_file // empty' "$FJ_MANIFEST")
        FJ_DB_BYTES=$(${pkgs.jq}/bin/jq -r '.database_bytes // 0' "$FJ_MANIFEST")

        if [ "$FJ_STATUS" != "success" ]; then
          fail "Forgejo dump manifest reported failure status: $FJ_STATUS"
        fi
        if [ "$FJ_CYCLE" != "$CURRENT_CYCLE" ]; then
          fail "Forgejo dump is stale: manifest cycle $FJ_CYCLE does not match current cycle $CURRENT_CYCLE"
        fi
        if [ "$((NOW_EPOCH - FJ_TS))" -gt "$MAX_MANIFEST_AGE" ]; then
          fail "Forgejo dump manifest timestamp too old ($((NOW_EPOCH - FJ_TS))s > $MAX_MANIFEST_AGE s)"
        fi
        if [ ! -s "$NAS_MOUNT/forgejo/backups/$FJ_DB_FILE" ]; then
          fail "Forgejo database file $FJ_DB_FILE missing or empty"
        fi
        if [ "$FJ_DB_BYTES" -lt 10485760 ]; then
          fail "Forgejo database suspiciously small ($FJ_DB_BYTES bytes < 10MB)"
        fi
        echo "Validated Forgejo cycle $CURRENT_CYCLE dump: $FJ_DB_FILE ($(($FJ_DB_BYTES / 1024 / 1024)) MB)"

        # 3. Platform state manifest verification
        PS_MANIFEST="$NAS_MOUNT/backups/platform-state/backup-manifest.json"
        if [ ! -s "$PS_MANIFEST" ]; then
          fail "Platform state backup manifest missing or empty at $PS_MANIFEST"
        fi
        PS_STATUS=$(${pkgs.jq}/bin/jq -r '.status // empty' "$PS_MANIFEST")
        PS_CYCLE=$(${pkgs.jq}/bin/jq -r '.cycle // empty' "$PS_MANIFEST")
        PS_TS=$(${pkgs.jq}/bin/jq -r '.timestamp // 0' "$PS_MANIFEST")
        PS_TOFU_BYTES=$(${pkgs.jq}/bin/jq -r '.tofu_state_bytes // 0' "$PS_MANIFEST")

        if [ "$PS_STATUS" != "success" ]; then
          fail "Platform state manifest reported failure status: $PS_STATUS"
        fi
        if [ "$PS_CYCLE" != "$CURRENT_CYCLE" ]; then
          fail "Platform state dump is stale: manifest cycle $PS_CYCLE does not match current cycle $CURRENT_CYCLE"
        fi
        if [ "$((NOW_EPOCH - PS_TS))" -gt "$MAX_MANIFEST_AGE" ]; then
          fail "Platform state manifest timestamp too old ($((NOW_EPOCH - PS_TS))s > $MAX_MANIFEST_AGE s)"
        fi
        if [ ! -s "$NAS_MOUNT/backups/platform-state/tofu-state/state.sqlite" ]; then
          fail "OpenTofu state sqlite file missing or empty"
        fi
        if [ "$PS_TOFU_BYTES" -lt 10485760 ]; then
          fail "OpenTofu state suspiciously small ($PS_TOFU_BYTES bytes < 10MB)"
        fi
        echo "Validated Platform state cycle $CURRENT_CYCLE dump: tofu-state/state.sqlite ($(($PS_TOFU_BYTES / 1024 / 1024)) MB)"

        # 4. k3s manifest verification
        K3S_STATUS=$(${pkgs.jq}/bin/jq -r '.status // empty' "$K3S_MANIFEST")
        K3S_CYCLE=$(${pkgs.jq}/bin/jq -r '.cycle // empty' "$K3S_MANIFEST")
        K3S_TS=$(${pkgs.jq}/bin/jq -r '.timestamp // 0' "$K3S_MANIFEST")
        K3S_SNAP_FILE=$(${pkgs.jq}/bin/jq -r '.snapshot_file // empty' "$K3S_MANIFEST")
        K3S_SNAP_BYTES=$(${pkgs.jq}/bin/jq -r '.snapshot_bytes // 0' "$K3S_MANIFEST")

        if [ "$K3S_STATUS" != "success" ]; then
          fail "k3s backup prep reported failure status: $K3S_STATUS"
        fi
        if [ "$K3S_CYCLE" != "$CURRENT_CYCLE" ]; then
          fail "k3s backup prep is stale: manifest cycle $K3S_CYCLE does not match current cycle $CURRENT_CYCLE"
        fi
        if [ "$((NOW_EPOCH - K3S_TS))" -gt "$MAX_MANIFEST_AGE" ]; then
          fail "k3s backup prep manifest timestamp too old ($((NOW_EPOCH - K3S_TS))s > $MAX_MANIFEST_AGE s)"
        fi
        if [ ! -s "$K3S_MOUNT/etcd-snapshot-latest" ]; then
          fail "k3s etcd snapshot missing or empty at $K3S_MOUNT/etcd-snapshot-latest"
        fi
        if [ "$K3S_SNAP_BYTES" -lt 10485760 ]; then
          fail "k3s etcd snapshot suspiciously small ($K3S_SNAP_BYTES bytes < 10MB)"
        fi
        echo "Validated k3s cycle $CURRENT_CYCLE snapshot: $K3S_SNAP_FILE ($(($K3S_SNAP_BYTES / 1024 / 1024)) MB)"

        # 5. Immich & Home Assistant database dumps (from frozen snapshot)
        IMMICH_LIB_DIR=$(${pkgs.findutils}/bin/find "$NAS_CLUSTER_MOUNT" -maxdepth 1 -name "immich-immich-library-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)
        if [ -z "$IMMICH_LIB_DIR" ] || [ ! -d "$IMMICH_LIB_DIR/library" ] || [ ! -d "$IMMICH_LIB_DIR/upload" ]; then
          fail "Immich library or upload directory not found in $NAS_CLUSTER_MOUNT"
        fi

        DUMPS_DIR=$(${pkgs.findutils}/bin/find "$NAS_CLUSTER_MOUNT" -maxdepth 1 -name "backups-db-dumps-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)
        if [ -z "$DUMPS_DIR" ] || [ ! -d "$DUMPS_DIR" ]; then
          fail "DB dumps directory not found in $NAS_CLUSTER_MOUNT"
        fi

        LATEST_IMMICH_DUMP=$(${pkgs.findutils}/bin/find "$DUMPS_DIR" -name "immich-*.dump.gz" -mmin -1080 2>/dev/null | ${pkgs.coreutils}/bin/sort -r | ${pkgs.coreutils}/bin/head -n 1)
        if [ -z "$LATEST_IMMICH_DUMP" ]; then
          fail "No Immich DB dump from the current backup cycle (< 18 hours old) found in $DUMPS_DIR"
        fi
        IMMICH_DUMP_SIZE=$(${pkgs.coreutils}/bin/stat -c %s "$LATEST_IMMICH_DUMP")
        if [ "$IMMICH_DUMP_SIZE" -lt 10485760 ]; then
          fail "Immich DB dump $LATEST_IMMICH_DUMP suspiciously small ($IMMICH_DUMP_SIZE bytes < 10MB)"
        fi
        echo "Found fresh Immich DB dump: $LATEST_IMMICH_DUMP ($(($IMMICH_DUMP_SIZE / 1024 / 1024)) MB)"

        LATEST_HA_DUMP=$(${pkgs.findutils}/bin/find "$DUMPS_DIR" -name "home-assistant-*.dump.gz" -mmin -1080 2>/dev/null | ${pkgs.coreutils}/bin/sort -r | ${pkgs.coreutils}/bin/head -n 1)
        if [ -z "$LATEST_HA_DUMP" ]; then
          fail "No Home Assistant DB dump from the current backup cycle (< 18 hours old) found in $DUMPS_DIR"
        fi
        HA_DUMP_SIZE=$(${pkgs.coreutils}/bin/stat -c %s "$LATEST_HA_DUMP")
        if [ "$HA_DUMP_SIZE" -lt 1048576 ]; then
          fail "Home Assistant DB dump $LATEST_HA_DUMP suspiciously small ($HA_DUMP_SIZE bytes < 1MB)"
        fi
        echo "Found fresh Home Assistant DB dump: $LATEST_HA_DUMP ($(($HA_DUMP_SIZE / 1024 / 1024)) MB)"

        HA_CONFIG_DIR=$(${pkgs.findutils}/bin/find "$NAS_CLUSTER_MOUNT" -maxdepth 1 -name "home-assistant-home-assistant-config-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)
        if [ -z "$HA_CONFIG_DIR" ] || [ ! -f "$HA_CONFIG_DIR/configuration.yaml" ]; then
          fail "Home Assistant configuration directory or configuration.yaml not found"
        fi

        HA_MATTER_DIR=$(${pkgs.findutils}/bin/find "$NAS_CLUSTER_MOUNT" -maxdepth 1 -name "home-assistant-home-assistant-matter-server-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)

        if [ ! -d "$NAS_MOUNT/forgejo/repositories" ]; then
          fail "Forgejo repositories directory not found at $NAS_MOUNT/forgejo/repositories"
        fi

        FORGEJO_BACKUPS_DIR="$NAS_MOUNT/forgejo/backups"
        if [ ! -d "$FORGEJO_BACKUPS_DIR" ]; then
          fail "Forgejo backups directory not found at $FORGEJO_BACKUPS_DIR"
        fi

        # 6. Application PVCs (from frozen point-in-time ZFS snapshot)
        BOOKSHELF_DIR=$(${pkgs.findutils}/bin/find "$NAS_CLUSTER_MOUNT" -maxdepth 1 -name "media-bookshelf-config-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)
        SONARR_DIR=$(${pkgs.findutils}/bin/find "$NAS_CLUSTER_MOUNT" -maxdepth 1 -name "media-sonarr-config-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)
        RADARR_DIR=$(${pkgs.findutils}/bin/find "$NAS_CLUSTER_MOUNT" -maxdepth 1 -name "media-radarr-config-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)
        PROWLARR_DIR=$(${pkgs.findutils}/bin/find "$NAS_CLUSTER_MOUNT" -maxdepth 1 -name "media-prowlarr-config-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)
        CALIBRE_DIR=$(${pkgs.findutils}/bin/find "$NAS_CLUSTER_MOUNT" -maxdepth 1 -name "media-calibre-config-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)
        NAVIDROME_DIR=$(${pkgs.findutils}/bin/find "$NAS_CLUSTER_MOUNT" -maxdepth 1 -name "media-navidrome-data-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)
        COUCHDB_DIR=$(${pkgs.findutils}/bin/find "$NAS_CLUSTER_MOUNT" -maxdepth 1 -name "obsidian-couchdb-data-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)
        SLSKD_DIR=$(${pkgs.findutils}/bin/find "$NAS_CLUSTER_MOUNT" -maxdepth 1 -name "media-slskd-config-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)

        for app_dir in "$BOOKSHELF_DIR" "$SONARR_DIR" "$RADARR_DIR" "$PROWLARR_DIR" "$CALIBRE_DIR" "$NAVIDROME_DIR" "$COUCHDB_DIR"; do
          if [ -z "$app_dir" ] || [ ! -d "$app_dir" ]; then
            fail "Required application PVC directory missing from $NAS_CLUSTER_MOUNT"
          fi
        done
        echo "Found all required application PVCs in frozen ZFS snapshot"

        # Local Infrastructure state paths
        LOCAL_PATHS=(
          "/var/lib/private/AdGuardHome/AdGuardHome.yaml"
          "/var/lib/netbird"
          "/var/lib/mosquitto/mosquitto.db"
          "/var/lib/zigbee2mqtt"
          "/persist/etc/ssh/ssh_host_ed25519_key"
        )
        for loc in "''${LOCAL_PATHS[@]}"; do
          if [ ! -e "$loc" ]; then
            fail "Local infrastructure state path missing: $loc"
          fi
        done
        echo "Found local infrastructure state paths"

        echo "==> [4/5] Executing Restic backup to /mnt/backups/restic-repo..."
        export RESTIC_REPOSITORY="/mnt/backups/restic-repo"
        PASSWORD_FILE="''${CREDENTIALS_DIRECTORY}/password"
        if [ ! -f "$PASSWORD_FILE" ]; then
          PASSWORD_FILE="${config.sops.secrets.backup-restic-password.path}"
        fi
        export RESTIC_PASSWORD_FILE="$PASSWORD_FILE"

        if ! ${pkgs.restic}/bin/restic snapshots >/dev/null 2>&1; then
          echo "Initializing new Restic repository at $RESTIC_REPOSITORY..."
          ${pkgs.restic}/bin/restic init
        fi

        ${pkgs.restic}/bin/restic unlock || true

        SOURCES=(
          "$IMMICH_LIB_DIR/library"
          "$IMMICH_LIB_DIR/upload"
          "$IMMICH_LIB_DIR/profile"
          "$DUMPS_DIR"
          "$HA_CONFIG_DIR"
          "$NAS_MOUNT/forgejo/repositories"
          "$FORGEJO_BACKUPS_DIR"
          "$PLATFORM_STATE_DIR"
          "$NAS_MOUNT/s3"
          "$BOOKSHELF_DIR"
          "$CALIBRE_DIR"
          "$SONARR_DIR"
          "$RADARR_DIR"
          "$PROWLARR_DIR"
          "$NAVIDROME_DIR"
          "$COUCHDB_DIR"
          "$K3S_MOUNT"
          "$WOODPECKER_MOUNT"
          "''${LOCAL_PATHS[@]}"
        )
        if [ -n "$HA_MATTER_DIR" ] && [ -d "$HA_MATTER_DIR" ]; then
          SOURCES+=("$HA_MATTER_DIR")
        fi
        if [ -n "$SLSKD_DIR" ] && [ -d "$SLSKD_DIR" ]; then
          SOURCES+=("$SLSKD_DIR")
        fi

        RC=0
        ${pkgs.restic}/bin/restic backup \
          --host adguard-netbird-01 \
          --group-by host \
          --tag immich \
          --tag databases \
          --tag forgejo \
          --tag home-assistant \
          --tag k3s \
          --tag applications \
          --tag infrastructure \
          --exclude '.storage' \
          --exclude 'thumbs' \
          --exclude 'encoded-video' \
          --exclude 'tmp_objdir-*' \
          --exclude '*/custom_components/spook_*' \
          --exclude 'Backups' \
          --exclude 'MediaCover' \
          --exclude 'logs' \
          --exclude 'logs.db' \
          --exclude '*.pid' \
          --exclude '*.sock' \
          --exclude 'Sentry' \
          --exclude 'asp' \
          --exclude 'cache' \
          --exclude '.cache' \
          --exclude 'tmp' \
          --exclude 'lost+found' \
          --exclude '*.tmp' \
          "''${SOURCES[@]}" || RC=$?

        if [ "$RC" -eq 0 ] || [ "$RC" -eq 3 ]; then
          NOW=$(date +%s)
          record_metric 1 "$NOW"
          echo "==> [5/5] Backup completed successfully (status: $RC) at $(date -d @$NOW)!"
        else
          fail "Restic backup failed with exit code $RC"
        fi
  '';

  pruneScript = pkgs.writeShellScript "run-immich-external-prune" ''
    set -euo pipefail

    exec 200>"/run/lock/immich-external-ops.lock"
    if ! ${pkgs.util-linux}/bin/flock -n 200; then
      echo "Another external backup operation (backup, prune, or check) is currently running. Exiting." >&2
      exit 1
    fi

    is_unit_running() {
      local state
      state=$(${pkgs.systemd}/bin/systemctl show -p ActiveState --value "$1" 2>/dev/null || true)
      [ "$state" = "active" ] || [ "$state" = "activating" ]
    }

    if is_unit_running immich-external-backup.service; then
      echo "Refusing to prune: immich-external-backup.service is currently running" >&2
      exit 1
    fi
    if is_unit_running immich-external-backup-check.service; then
      echo "Refusing to prune: immich-external-backup-check.service is currently running" >&2
      exit 1
    fi

    PROM_FILE="/var/lib/node-exporter-textfile/immich_backup.prom"
    if [ ! -f "$PROM_FILE" ]; then
      echo "Refusing to prune: no prior backup status found at $PROM_FILE" >&2
      exit 1
    fi

    LAST_STATUS=$(${pkgs.gnugrep}/bin/grep "^immich_backup_last_status " "$PROM_FILE" | ${pkgs.gawk}/bin/awk '{print $2}')
    if [ "$LAST_STATUS" != "1" ]; then
      echo "Refusing to prune: last backup attempt was not successful (status: $LAST_STATUS)" >&2
      exit 1
    fi

    LAST_SUCCESS=$(${pkgs.gnugrep}/bin/grep "^immich_backup_last_success_timestamp_seconds " "$PROM_FILE" | ${pkgs.gawk}/bin/awk '{print $2}')
    if [ -z "$LAST_SUCCESS" ] || [ "$LAST_SUCCESS" -le 0 ]; then
      echo "Refusing to prune: no valid last successful backup timestamp found" >&2
      exit 1
    fi

    NOW=$(date +%s)
    AGE=$(( NOW - LAST_SUCCESS ))
    MAX_AGE=$(( 36 * 3600 ))
    if [ "$AGE" -gt "$MAX_AGE" ]; then
      echo "Refusing to prune: last successful backup is too old ($(( AGE / 3600 )) hours old; max allowed is 36 hours)" >&2
      exit 1
    fi

    export RESTIC_REPOSITORY="/mnt/backups/restic-repo"
    PASSWORD_FILE="''${CREDENTIALS_DIRECTORY}/password"
    if [ ! -f "$PASSWORD_FILE" ]; then
      PASSWORD_FILE="${config.sops.secrets.backup-restic-password.path}"
    fi
    export RESTIC_PASSWORD_FILE="$PASSWORD_FILE"

    echo "Verifying latest snapshot freshness before pruning..."
    LATEST_JSON=$(${pkgs.restic}/bin/restic snapshots --json --latest 1)
    if [ "$LATEST_JSON" = "[]" ] || [ -z "$LATEST_JSON" ]; then
      echo "Refusing to prune: no snapshots exist in repository!" >&2
      exit 1
    fi

    ${pkgs.restic}/bin/restic unlock || true
    ${pkgs.restic}/bin/restic forget \
      --group-by host \
      --keep-last 3 \
      --keep-daily 14 \
      --keep-weekly 8 \
      --keep-monthly 3 \
      --prune
    echo "Prune completed successfully."
  '';

  checkScript = pkgs.writeShellScript "run-immich-external-check" ''
    set -euo pipefail

    exec 200>"/run/lock/immich-external-ops.lock"
    if ! ${pkgs.util-linux}/bin/flock -n 200; then
      echo "Another external backup operation (backup, prune, or check) is currently running. Exiting." >&2
      exit 1
    fi

    is_unit_running() {
      local state
      state=$(${pkgs.systemd}/bin/systemctl show -p ActiveState --value "$1" 2>/dev/null || true)
      [ "$state" = "active" ] || [ "$state" = "activating" ]
    }

    if is_unit_running immich-external-backup.service; then
      echo "Refusing to check: immich-external-backup.service is currently running" >&2
      exit 1
    fi
    if is_unit_running immich-external-backup-prune.service; then
      echo "Refusing to check: immich-external-backup-prune.service is currently running" >&2
      exit 1
    fi

    export RESTIC_REPOSITORY="/mnt/backups/restic-repo"
    PASSWORD_FILE="''${CREDENTIALS_DIRECTORY}/password"
    if [ ! -f "$PASSWORD_FILE" ]; then
      PASSWORD_FILE="${config.sops.secrets.backup-restic-password.path}"
    fi
    export RESTIC_PASSWORD_FILE="$PASSWORD_FILE"

    ${pkgs.restic}/bin/restic unlock || true
    echo "Running integrity check with data verification sample..."
    ${pkgs.restic}/bin/restic check --read-data-subset=1/10
    echo "Integrity check passed."
  '';
in
{
  sops.secrets.backup-restic-password = { };
  sops.secrets.backup-reader-ssh-key = { };

  environment.systemPackages = [
    pkgs.restic
    pkgs.sshfs
    pkgs.smartmontools
  ];

  services.smartd = {
    enable = true;
    devices = [
      {
        device = "/dev/disk/by-id/ata-HGST_HTS721010A9E630_JR1004BNJSUYAM";
        options = "-d sat -d removable -a -o on -S on -s (S/../.././02|L/../../7/04)";
      }
    ];
  };

  systemd.services.immich-external-backup = {
    description = "Pull Immich, DB dumps, and Forgejo state to 1TB external HDD";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    unitConfig.RequiresMountsFor = [ "/mnt/backups" ];
    path = [
      pkgs.coreutils
      pkgs.util-linux
      pkgs.findutils
      pkgs.gawk
      pkgs.gnugrep
      pkgs.jq
      pkgs.sshfs
      pkgs.fuse
      pkgs.restic
      pkgs.openssh
      pkgs.systemd
    ];
    environment = {
      XDG_CACHE_HOME = "/var/cache/immich-external-backup";
      HOME = "/var/cache/immich-external-backup";
    };
    serviceConfig = {
      Type = "oneshot";
      TimeoutStartSec = "4h";
      ExecStart = backupScript;
      CacheDirectory = "immich-external-backup";
      LoadCredential = [
        "password:${config.sops.secrets.backup-restic-password.path}"
        "ssh_key:${config.sops.secrets.backup-reader-ssh-key.path}"
      ];
    };
  };

  systemd.timers.immich-external-backup = {
    description = "Timer for nightly external pull backup";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "03:30:00";
      Persistent = true;
      RandomizedDelaySec = "5m";
    };
  };

  systemd.services.immich-external-backup-prune = {
    description = "Prune external backup repository with retention policy";
    unitConfig.RequiresMountsFor = [ "/mnt/backups" ];
    path = [
      pkgs.coreutils
      pkgs.util-linux
      pkgs.systemd
      pkgs.gnugrep
      pkgs.gawk
      pkgs.restic
    ];
    environment = {
      XDG_CACHE_HOME = "/var/cache/immich-external-backup";
      HOME = "/var/cache/immich-external-backup";
    };
    serviceConfig = {
      Type = "oneshot";
      TimeoutStartSec = "2h";
      ExecStart = pruneScript;
      CacheDirectory = "immich-external-backup";
      LoadCredential = [
        "password:${config.sops.secrets.backup-restic-password.path}"
      ];
    };
  };

  systemd.timers.immich-external-backup-prune = {
    description = "Timer for external backup pruning";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "Sun *-*-* 06:00:00";
      Persistent = true;
      RandomizedDelaySec = "10m";
    };
  };

  systemd.services.immich-external-backup-check = {
    description = "Verify integrity of external backup repository";
    unitConfig.RequiresMountsFor = [ "/mnt/backups" ];
    path = [
      pkgs.coreutils
      pkgs.util-linux
      pkgs.systemd
      pkgs.restic
    ];
    environment = {
      XDG_CACHE_HOME = "/var/cache/immich-external-backup";
      HOME = "/var/cache/immich-external-backup";
    };
    serviceConfig = {
      Type = "oneshot";
      TimeoutStartSec = "2h";
      ExecStart = checkScript;
      CacheDirectory = "immich-external-backup";
      LoadCredential = [
        "password:${config.sops.secrets.backup-restic-password.path}"
      ];
    };
  };

  systemd.timers.immich-external-backup-check = {
    description = "Timer for weekly integrity check of external backup repository";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "Sun *-*-* 08:00:00";
      Persistent = true;
      RandomizedDelaySec = "15m";
    };
  };

  systemd.tmpfiles.rules = [
    "d /mnt/backups 0700 root root -"
    "d /mnt/backups/restic-repo 0700 root root -"
    "d /run/backup-source 0700 root root -"
  ];
}
