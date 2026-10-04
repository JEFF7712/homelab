{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.platformBackup;
  source = pkgs.runCommand "platform-backup-source" { } ''
    mkdir -p $out/scripts/ci
    cp ${../../scripts/ci/platform_backup.py} $out/scripts/ci/platform_backup.py
    touch $out/scripts/__init__.py $out/scripts/ci/__init__.py
  '';
in
{
  options.homelab.platformBackup.enable = lib.mkEnableOption "Consistent platform snapshots to encrypted offsite Restic";
  config = lib.mkIf cfg.enable {
    systemd.services.platform-backup = {
      description = "Back up Git, CI, state, and recovery credentials";
      path = [
        pkgs.restic
        pkgs.systemd
        pkgs.zfs
      ];
      environment.PYTHONPATH = toString source;
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${pkgs.python313}/bin/python -m scripts.ci.platform_backup --host ${config.networking.hostName}";
        EnvironmentFile = "/persist/platform-backup.env";
        StateDirectory = "platform-backup";
        UMask = "0077";
      };
    };
    systemd.timers.platform-backup = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "hourly";
        Persistent = true;
        RandomizedDelaySec = "5m";
      };
    };
  };
}
