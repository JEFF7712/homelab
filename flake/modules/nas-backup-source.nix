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
        ${pkgs.coreutils}/bin/ln -sf "$DUMP_FILE" "$BACKUP_DIR/forgejo-latest.sqlite"
        ${pkgs.findutils}/bin/find "$BACKUP_DIR" -name 'forgejo-*.sqlite' -mtime +14 -delete
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
}
