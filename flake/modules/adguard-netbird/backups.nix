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

        echo "==> [1/5] Verifying backup destination /mnt/backups..."
        if ! ${pkgs.util-linux}/bin/mountpoint -q /mnt/backups; then
          fail "/mnt/backups is not a mounted filesystem"
        fi

        if [ ! -f /mnt/backups/.backup-target-canary ]; then
          fail "Canary file /mnt/backups/.backup-target-canary is missing"
        fi

        FREE_KB=$(${pkgs.coreutils}/bin/df -k --output=avail /mnt/backups | ${pkgs.gawk}/bin/awk 'NR==2 {print $1}')
        if [ "$FREE_KB" -lt 15728640 ]; then
          fail "Insufficient free space on /mnt/backups (< 15 GiB free: $FREE_KB KB)"
        fi

        echo "==> [2/5] Mounting remote source nas-01 via read-only SSHFS..."
        MOUNT_DIR="/run/backup-source"
        ${pkgs.coreutils}/bin/mkdir -p "$MOUNT_DIR"

        cleanup() {
          local exit_code=$?
          if ${pkgs.util-linux}/bin/mountpoint -q "$MOUNT_DIR" 2>/dev/null; then
            echo "Cleaning up: unmounting $MOUNT_DIR..."
            ${pkgs.fuse}/bin/fusermount -u "$MOUNT_DIR" 2>/dev/null || ${pkgs.util-linux}/bin/umount -l "$MOUNT_DIR" 2>/dev/null || true
          fi
          ${pkgs.coreutils}/bin/rmdir "$MOUNT_DIR" 2>/dev/null || true
          return $exit_code
        }
        trap cleanup EXIT

        SSH_KEY="''${CREDENTIALS_DIRECTORY}/ssh_key"
        if [ ! -f "$SSH_KEY" ]; then
          SSH_KEY="${config.sops.secrets.backup-reader-ssh-key.path}"
        fi

        ${pkgs.sshfs}/bin/sshfs backup-reader@10.0.30.20:/tank "$MOUNT_DIR" \
          -o ro,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3 \
          -o IdentityFile="$SSH_KEY" \
          -o StrictHostKeyChecking=accept-new

        echo "==> [3/5] Validating source paths and content freshness..."
        IMMICH_LIB_DIR=$(${pkgs.findutils}/bin/find "$MOUNT_DIR/cluster" -maxdepth 1 -name "immich-immich-library-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)
        if [ -z "$IMMICH_LIB_DIR" ] || [ ! -d "$IMMICH_LIB_DIR/library" ] || [ ! -d "$IMMICH_LIB_DIR/upload" ]; then
          fail "Immich library or upload directory not found in $MOUNT_DIR/cluster"
        fi

        DUMPS_DIR=$(${pkgs.findutils}/bin/find "$MOUNT_DIR/cluster" -maxdepth 1 -name "backups-db-dumps-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)
        if [ -z "$DUMPS_DIR" ] || [ ! -d "$DUMPS_DIR" ]; then
          fail "DB dumps directory not found in $MOUNT_DIR/cluster"
        fi

        # Database freshness and size checks
        LATEST_IMMICH_DUMP=$(${pkgs.findutils}/bin/find "$DUMPS_DIR" -name "immich-*.dump.gz" -mtime -2 2>/dev/null | ${pkgs.coreutils}/bin/sort -r | ${pkgs.coreutils}/bin/head -n 1)
        if [ -z "$LATEST_IMMICH_DUMP" ]; then
          fail "No Immich DB dump less than 48 hours old found in $DUMPS_DIR"
        fi
        IMMICH_DUMP_SIZE=$(${pkgs.coreutils}/bin/stat -c %s "$LATEST_IMMICH_DUMP")
        if [ "$IMMICH_DUMP_SIZE" -lt 10485760 ]; then
          fail "Immich DB dump $LATEST_IMMICH_DUMP suspiciously small ($IMMICH_DUMP_SIZE bytes < 10MB)"
        fi
        echo "Found fresh Immich DB dump: $LATEST_IMMICH_DUMP ($(($IMMICH_DUMP_SIZE / 1024 / 1024)) MB)"

        LATEST_HA_DUMP=$(${pkgs.findutils}/bin/find "$DUMPS_DIR" -name "home-assistant-*.dump.gz" -mtime -2 2>/dev/null | ${pkgs.coreutils}/bin/sort -r | ${pkgs.coreutils}/bin/head -n 1)
        if [ -z "$LATEST_HA_DUMP" ]; then
          fail "No Home Assistant DB dump less than 48 hours old found in $DUMPS_DIR"
        fi
        HA_DUMP_SIZE=$(${pkgs.coreutils}/bin/stat -c %s "$LATEST_HA_DUMP")
        if [ "$HA_DUMP_SIZE" -lt 1048576 ]; then
          fail "Home Assistant DB dump $LATEST_HA_DUMP suspiciously small ($HA_DUMP_SIZE bytes < 1MB)"
        fi
        echo "Found fresh Home Assistant DB dump: $LATEST_HA_DUMP ($(($HA_DUMP_SIZE / 1024 / 1024)) MB)"

        HA_CONFIG_DIR=$(${pkgs.findutils}/bin/find "$MOUNT_DIR/cluster" -maxdepth 1 -name "home-assistant-home-assistant-config-pvc-*" 2>/dev/null | ${pkgs.coreutils}/bin/head -n 1)
        if [ -z "$HA_CONFIG_DIR" ] || [ ! -f "$HA_CONFIG_DIR/configuration.yaml" ]; then
          fail "Home Assistant configuration directory or configuration.yaml not found"
        fi

        if [ ! -d "$MOUNT_DIR/forgejo/repositories" ]; then
          fail "Forgejo repositories directory not found at $MOUNT_DIR/forgejo/repositories"
        fi

        FORGEJO_DUMP="$MOUNT_DIR/forgejo/backups/forgejo-latest.sqlite"
        if [ ! -f "$FORGEJO_DUMP" ]; then
          FORGEJO_DUMP=$(${pkgs.findutils}/bin/find "$MOUNT_DIR/forgejo/backups" -name "forgejo-*.sqlite" 2>/dev/null | ${pkgs.coreutils}/bin/sort -r | ${pkgs.coreutils}/bin/head -n 1)
        fi
        if [ -z "$FORGEJO_DUMP" ] || [ ! -f "$FORGEJO_DUMP" ]; then
          fail "Forgejo SQLite database dump not found in $MOUNT_DIR/forgejo/backups"
        fi
        echo "Found Forgejo DB dump: $FORGEJO_DUMP"

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
          "$MOUNT_DIR/forgejo/repositories"
          "$FORGEJO_DUMP"
        )

        ${pkgs.restic}/bin/restic backup \
          --tag immich \
          --tag databases \
          --tag forgejo \
          --tag home-assistant \
          --exclude '.storage' \
          --exclude 'thumbs' \
          --exclude 'encoded-video' \
          "''${SOURCES[@]}"

        NOW=$(date +%s)
        record_metric 1 "$NOW"
        echo "==> [5/5] Backup completed successfully at $(date -d @$NOW)!"
  '';

  pruneScript = pkgs.writeShellScript "run-immich-external-prune" ''
    set -euo pipefail

    PROM_FILE="/var/lib/node-exporter-textfile/immich_backup.prom"
    if [ -f "$PROM_FILE" ]; then
      LAST_STATUS=$(${pkgs.gnugrep}/bin/grep "^immich_backup_last_status " "$PROM_FILE" | ${pkgs.gawk}/bin/awk '{print $2}')
      if [ "$LAST_STATUS" != "1" ]; then
        echo "Refusing to prune: last backup attempt was not successful (status: $LAST_STATUS)" >&2
        exit 1
      fi
    else
      echo "Refusing to prune: no prior backup status found at $PROM_FILE" >&2
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
      --keep-daily 14 \
      --keep-weekly 8 \
      --keep-monthly 3 \
      --prune
    echo "Prune completed successfully."
  '';

  checkScript = pkgs.writeShellScript "run-immich-external-check" ''
    set -euo pipefail
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
      pkgs.sshfs
      pkgs.fuse
      pkgs.restic
      pkgs.openssh
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

  # Automatic pruning disabled until initial recovery drill has been demonstrated
  systemd.timers.immich-external-backup-prune = {
    description = "Timer for external backup pruning";
    enable = false;
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "Sun *-*-* 04:30:00";
      Persistent = true;
    };
  };

  systemd.services.immich-external-backup-check = {
    description = "Verify integrity of external backup repository";
    unitConfig.RequiresMountsFor = [ "/mnt/backups" ];
    path = [
      pkgs.coreutils
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
      OnCalendar = "Sun *-*-* 05:00:00";
      Persistent = true;
      RandomizedDelaySec = "15m";
    };
  };
}
