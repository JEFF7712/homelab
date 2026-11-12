{
  config,
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
    path = [
      config.services.k3s.package
      pkgs.etcd
    ];
    serviceConfig = {
      Type = "oneshot";
      TimeoutStartSec = "15min";
      UMask = "0077";
      ExecStart = "${pkgs.python3}/bin/python ${../../scripts/k3s_backup_source.py} prepare";
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
