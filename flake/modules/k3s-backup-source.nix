{
  pkgs,
  ...
}:
{
  users.users.backup-reader = {
    isSystemUser = true;
    group = "backup-reader";
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

  systemd.services.k3s-backup-prep = {
    description = "Prepare k3s etcd snapshots and server tokens for external backup";
    after = [ "k3s.service" ];
    unitConfig.RequiresMountsFor = [ "/persist" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = pkgs.writeShellScript "k3s-backup-prep" ''
                set -euo pipefail
                EXPORT_DIR="/persist/backup-source/k3s"
                ${pkgs.coreutils}/bin/install -d -m 0750 -o root -g backup-reader "$EXPORT_DIR"

                # Verify that an etcd snapshot taken in the current cycle (< 180 minutes ago) exists
                LATEST_SNAP=$(${pkgs.findutils}/bin/find /persist/etcd-snapshots -name "etcd-snapshot-homelab-01-*" -mmin -180 2>/dev/null | ${pkgs.coreutils}/bin/sort -r | ${pkgs.coreutils}/bin/head -n 1)
                if [ -z "$LATEST_SNAP" ] || [ ! -s "$LATEST_SNAP" ]; then
                  # Fallback to latest snapshot within 24h if cluster schedule jitter occurred
                  LATEST_SNAP=$(${pkgs.findutils}/bin/find /persist/etcd-snapshots -name "etcd-snapshot-homelab-01-*" -mtime -1 2>/dev/null | ${pkgs.coreutils}/bin/sort -r | ${pkgs.coreutils}/bin/head -n 1)
                fi
                if [ -z "$LATEST_SNAP" ] || [ ! -s "$LATEST_SNAP" ]; then
                  echo "ERROR: No recent valid etcd snapshot found in /persist/etcd-snapshots" >&2
                  exit 1
                fi

                ${pkgs.coreutils}/bin/ln -f "$LATEST_SNAP" "$EXPORT_DIR/etcd-snapshot-latest"
                ${pkgs.coreutils}/bin/chmod 0640 "$EXPORT_DIR/etcd-snapshot-latest"
                ${pkgs.coreutils}/bin/chown root:backup-reader "$EXPORT_DIR/etcd-snapshot-latest"

                if [ -f /var/lib/rancher/k3s/server/token ]; then
                  ${pkgs.coreutils}/bin/cp -f /var/lib/rancher/k3s/server/token "$EXPORT_DIR/server-token"
                fi
                if [ -f /persist/etc/ssh/ssh_host_ed25519_key ]; then
                  ${pkgs.coreutils}/bin/cp -f /persist/etc/ssh/ssh_host_ed25519_key "$EXPORT_DIR/homelab-01-ssh_host_ed25519_key"
                fi

                # Write cycle manifest atomically for backup freshness verification
                CYCLE=$(${pkgs.coreutils}/bin/date +%Y%m%d)
                TIMESTAMP=$(${pkgs.coreutils}/bin/date +%s)
                SNAP_SIZE=$(${pkgs.coreutils}/bin/stat -c %s "$EXPORT_DIR/etcd-snapshot-latest")
                SNAP_NAME=$(${pkgs.coreutils}/bin/basename "$LATEST_SNAP")
                MANIFEST_TMP="$EXPORT_DIR/backup-manifest.json.tmp"
                MANIFEST="$EXPORT_DIR/backup-manifest.json"
                ${pkgs.coreutils}/bin/cat <<EOF > "$MANIFEST_TMP"
        {
          "job": "k3s-backup-prep",
          "cycle": "$CYCLE",
          "timestamp": $TIMESTAMP,
          "status": "success",
          "snapshot_file": "$SNAP_NAME",
          "snapshot_bytes": $SNAP_SIZE
        }
        EOF
                ${pkgs.coreutils}/bin/chmod 0640 "$MANIFEST_TMP"
                ${pkgs.coreutils}/bin/chown root:backup-reader "$MANIFEST_TMP"
                ${pkgs.coreutils}/bin/mv -f "$MANIFEST_TMP" "$MANIFEST"

                ${pkgs.coreutils}/bin/chown -R root:backup-reader "$EXPORT_DIR"
                ${pkgs.coreutils}/bin/chmod -R g+rX "$EXPORT_DIR"
      '';
    };
  };

  systemd.timers.k3s-backup-prep = {
    description = "Timer to prepare k3s etcd snapshots for external backup";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "03:15:00";
      Persistent = true;
    };
  };
}
