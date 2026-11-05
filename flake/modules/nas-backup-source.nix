{
  pkgs,
  ...
}:
{
  users.users.backup-reader = {
    isSystemUser = true;
    group = "backup-reader";
    extraGroups = [ "forgejo" ];
    shell = "${pkgs.shadow}/bin/nologin";
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIX4TNlD6vmGfSi+IZL4qkvg8gUzhEuRM+W3H1eZqZXT backup-reader@adguard-netbird-01"
    ];
  };
  users.groups.backup-reader = { };

  services.openssh.extraConfig = ''
    Match User backup-reader
      ForceCommand internal-sftp -R
      AllowTcpForwarding no
      AllowAgentForwarding no
      X11Forwarding no
      PermitTTY no
      PasswordAuthentication no
  '';

  systemd.services.forgejo-backup-dump = {
    description = "Create consistent SQLite dump of Forgejo for backups";
    after = [
      "forgejo.service"
      "tank-forgejo.mount"
    ];
    unitConfig.RequiresMountsFor = [
      "/tank/forgejo"
      "/persist/forgejo"
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "forgejo";
      Group = "forgejo";
      ExecStart = pkgs.writeShellScript "forgejo-db-dump" ''
        set -euo pipefail
        BACKUP_DIR="/tank/forgejo/backups"
        ${pkgs.coreutils}/bin/install -d -m 0750 -o forgejo -g forgejo "$BACKUP_DIR"
        STAMP=$(${pkgs.coreutils}/bin/date +%Y%m%d)
        DUMP_FILE="$BACKUP_DIR/forgejo-$STAMP.sqlite"
        TEMP_FILE="$BACKUP_DIR/forgejo-$STAMP.tmp.sqlite"
        ${pkgs.sqlite}/bin/sqlite3 /persist/forgejo/data/forgejo.db ".backup $TEMP_FILE"
        ${pkgs.coreutils}/bin/chmod 0640 "$TEMP_FILE"
        ${pkgs.coreutils}/bin/chown forgejo:forgejo "$TEMP_FILE"
        ${pkgs.coreutils}/bin/mv -f "$TEMP_FILE" "$DUMP_FILE"
        ${pkgs.coreutils}/bin/ln -sf "forgejo-$STAMP.sqlite" "$BACKUP_DIR/forgejo-latest.sqlite"
        ${pkgs.findutils}/bin/find "$BACKUP_DIR" -name 'forgejo-*.sqlite' -mtime +14 -delete

        # Stage non-database state (attachments, avatars, jwt, ssh keys, config)
        STATE_DIR="$BACKUP_DIR/state"
        ${pkgs.coreutils}/bin/install -d -m 0750 -o forgejo -g forgejo "$STATE_DIR"
        for item in attachments avatars jwt ssh; do
          if [ -d "/persist/forgejo/data/$item" ]; then
            ${pkgs.coreutils}/bin/cp -a "/persist/forgejo/data/$item" "$STATE_DIR/"
          fi
        done
        if [ -d "/persist/forgejo/conf" ]; then
          ${pkgs.coreutils}/bin/cp -a "/persist/forgejo/conf" "$STATE_DIR/"
        fi
      '';
    };
  };

  systemd.timers.forgejo-backup-dump = {
    description = "Timer for nightly Forgejo SQLite dump";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "02:50:00";
      Persistent = true;
    };
  };

  systemd.services.platform-state-backup-dump = {
    description = "Prepare OpenTofu state, Garage metadata, and encryption keys for external backup";
    after = [
      "tofu-state.service"
      "tank.mount"
    ];
    unitConfig.RequiresMountsFor = [
      "/tank"
      "/persist"
    ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = pkgs.writeShellScript "platform-state-backup-dump" ''
        set -euo pipefail
        BACKUP_DIR="/tank/backups/platform-state"
        ${pkgs.coreutils}/bin/install -d -m 0750 -o root -g backup-reader "$BACKUP_DIR"

        # 1. OpenTofu state safe SQLite backup
        TOFU_DIR="$BACKUP_DIR/tofu-state"
        ${pkgs.coreutils}/bin/install -d -m 0750 -o root -g backup-reader "$TOFU_DIR"
        if [ -f /persist/tofu-state/state.sqlite ]; then
          ${pkgs.sqlite}/bin/sqlite3 /persist/tofu-state/state.sqlite ".backup $TOFU_DIR/state.sqlite.tmp"
          ${pkgs.coreutils}/bin/mv -f "$TOFU_DIR/state.sqlite.tmp" "$TOFU_DIR/state.sqlite"
        fi

        # 2. Garage metadata
        GARAGE_DIR="$BACKUP_DIR/garage"
        ${pkgs.coreutils}/bin/install -d -m 0750 -o root -g backup-reader "$GARAGE_DIR"
        if [ -d /persist/garage ]; then
          ${pkgs.coreutils}/bin/cp -a /persist/garage/* "$GARAGE_DIR/" 2>/dev/null || true
        fi

        # 3. ZFS encryption keys and SSH host key
        KEYS_DIR="$BACKUP_DIR/keys"
        ${pkgs.coreutils}/bin/install -d -m 0750 -o root -g backup-reader "$KEYS_DIR"
        if [ -d /persist/keys ]; then
          ${pkgs.coreutils}/bin/cp -a /persist/keys/* "$KEYS_DIR/" 2>/dev/null || true
        fi
        if [ -f /persist/secrets/registry-auth.json ]; then
          ${pkgs.coreutils}/bin/cp -a /persist/secrets/registry-auth.json "$KEYS_DIR/"
        fi
        if [ -f /persist/etc/ssh/ssh_host_ed25519_key ]; then
          ${pkgs.coreutils}/bin/cp -a /persist/etc/ssh/ssh_host_ed25519_key "$KEYS_DIR/nas-01-ssh_host_ed25519_key"
        fi

        ${pkgs.coreutils}/bin/chown -R root:backup-reader "$BACKUP_DIR"
        ${pkgs.coreutils}/bin/chmod -R g+rX "$BACKUP_DIR"
      '';
    };
  };

  systemd.timers.platform-state-backup-dump = {
    description = "Timer for nightly platform state dump";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "02:55:00";
      Persistent = true;
    };
  };
}
