{
  pkgs,
  ...
}:
{
  users.users.backup-reader = {
    isSystemUser = true;
    group = "backup-reader";
    extraGroups = [ "woodpecker" ];
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

  systemd.services.woodpecker-backup-dump = {
    description = "Create consistent SQLite dump of Woodpecker CI for backups";
    after = [ "woodpecker-server.service" ];
    unitConfig.RequiresMountsFor = [ "/persist/woodpecker" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = pkgs.writeShellScript "woodpecker-backup-dump" ''
                        set -euo pipefail
                        BACKUP_DIR="/persist/woodpecker/backups"
                        ${pkgs.coreutils}/bin/install -d -m 0750 -o root -g backup-reader "$BACKUP_DIR"
                        STAMP=$(${pkgs.coreutils}/bin/date +%Y%m%d)
                        DUMP_FILE="$BACKUP_DIR/woodpecker-$STAMP.sqlite"
                        TEMP_FILE="$BACKUP_DIR/woodpecker-$STAMP.tmp.sqlite"

                        if [ -f /persist/woodpecker/server-data/woodpecker.sqlite ]; then
                          ${pkgs.python313}/bin/python -c 'import sqlite3, sys; src = sqlite3.connect("file:/persist/woodpecker/server-data/woodpecker.sqlite?mode=ro", uri=True); dst = sqlite3.connect(sys.argv[1]); src.backup(dst); dst.close(); src.close()' "$TEMP_FILE"
                          ${pkgs.coreutils}/bin/chmod 0640 "$TEMP_FILE"
                          ${pkgs.coreutils}/bin/chown root:backup-reader "$TEMP_FILE"
                          ${pkgs.coreutils}/bin/mv -f "$TEMP_FILE" "$DUMP_FILE"
                          ${pkgs.coreutils}/bin/ln -sf "woodpecker-$STAMP.sqlite" "$BACKUP_DIR/woodpecker-latest.sqlite"
                          ${pkgs.findutils}/bin/find "$BACKUP_DIR" -name 'woodpecker-*.sqlite' -mtime +14 -delete
                        fi

                        for name in signature-public.pem cloudflare-access-credentials.sops.json; do
                          if [ -f "/persist/woodpecker/$name" ]; then
                            ${pkgs.coreutils}/bin/cp -f "/persist/woodpecker/$name" "$BACKUP_DIR/"
                          fi
                        done
                        if [ -f /persist/etc/ssh/ssh_host_ed25519_key ]; then
                          ${pkgs.coreutils}/bin/cp -f /persist/etc/ssh/ssh_host_ed25519_key "$BACKUP_DIR/homelab-04-ssh_host_ed25519_key"
                        fi

                        # Write cycle manifest atomically for backup freshness verification
                        CYCLE=$(${pkgs.coreutils}/bin/date +%Y%m%d)
                        TIMESTAMP=$(${pkgs.coreutils}/bin/date +%s)
                        WP_SIZE=0
                        if [ -f "$DUMP_FILE" ]; then
                          WP_SIZE=$(${pkgs.coreutils}/bin/stat -c %s "$DUMP_FILE")
                        fi
                        MANIFEST_TMP="$BACKUP_DIR/backup-manifest.json.tmp"
                        MANIFEST="$BACKUP_DIR/backup-manifest.json"
                        ${pkgs.coreutils}/bin/cat <<EOF > "$MANIFEST_TMP"
        {
          "job": "woodpecker-backup-dump",
          "cycle": "$CYCLE",
          "timestamp": $TIMESTAMP,
          "status": "success",
          "database_file": "woodpecker-$STAMP.sqlite",
          "database_bytes": $WP_SIZE
        }
        EOF
                        ${pkgs.coreutils}/bin/chmod 0640 "$MANIFEST_TMP"
                        ${pkgs.coreutils}/bin/chown root:backup-reader "$MANIFEST_TMP"
                        ${pkgs.coreutils}/bin/mv -f "$MANIFEST_TMP" "$MANIFEST"

                        ${pkgs.coreutils}/bin/chown -R root:backup-reader "$BACKUP_DIR"
                        ${pkgs.coreutils}/bin/chmod -R g+rX "$BACKUP_DIR"
      '';
    };
  };

  systemd.timers.woodpecker-backup-dump = {
    description = "Timer for nightly Woodpecker CI SQLite dump";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "02:40:00";
      Persistent = true;
    };
  };
}
