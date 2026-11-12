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
  options.homelab.platformBackup = {
    enable = lib.mkEnableOption "Consistent platform snapshots to encrypted offsite Restic";
    bookshelfConfigPath = lib.mkOption {
      type = lib.types.str;
      default = "";
      description = "NAS cluster PVC directory containing Bookshelf configuration and SQLite state";
    };
    homeAssistantConfigPath = lib.mkOption {
      type = lib.types.str;
      default = "";
      description = "NAS cluster PVC directory containing the Home Assistant /config volume";
    };
    couchdbDataPath = lib.mkOption {
      type = lib.types.str;
      default = "";
      description = "NAS cluster PVC directory containing the CouchDB data volume; the drill opens its restored copy";
    };
  };
  config = lib.mkIf cfg.enable {
    # homelab-04 and nas-01 write to the same Restic repository.
    sops.secrets."platform-backup.env".sopsFile = ../../secrets/shared/platform-backup.sops.yaml;

    systemd.services.platform-backup = {
      description = "Back up Git, CI, state, and recovery credentials";
      unitConfig.RequiresMountsFor =
        "/persist"
        + lib.optionalString (
          cfg.bookshelfConfigPath != "" || cfg.homeAssistantConfigPath != "" || cfg.couchdbDataPath != ""
        ) " /tank/cluster";
      path = [
        pkgs.restic
        pkgs.systemd
        pkgs.zfs
      ];
      environment.PYTHONPATH = toString source;
      environment.XDG_CACHE_HOME = "/var/cache/platform-backup";
      serviceConfig = {
        Type = "oneshot";
        ExecStart =
          "${pkgs.python313}/bin/python -m scripts.ci.platform_backup --host ${config.networking.hostName} --mode backup"
          + lib.optionalString (
            cfg.bookshelfConfigPath != ""
          ) " --bookshelf-config ${lib.escapeShellArg cfg.bookshelfConfigPath}"
          + lib.optionalString (
            cfg.homeAssistantConfigPath != ""
          ) " --home-assistant-config ${lib.escapeShellArg cfg.homeAssistantConfigPath}"
          + lib.optionalString (
            cfg.couchdbDataPath != ""
          ) " --couchdb-data ${lib.escapeShellArg cfg.couchdbDataPath}";
        EnvironmentFile = config.sops.secrets."platform-backup.env".path;
        StateDirectory = "platform-backup";
        CacheDirectory = "platform-backup";
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

    systemd.services.platform-backup-maintenance = lib.mkIf (config.networking.hostName == "nas-01") {
      description = "Prune and verify platform backup repository";
      path = [
        pkgs.restic
      ];
      environment.PYTHONPATH = toString source;
      environment.XDG_CACHE_HOME = "/var/cache/platform-backup";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${pkgs.python313}/bin/python -m scripts.ci.platform_backup --host ${config.networking.hostName} --mode maintenance";
        EnvironmentFile = config.sops.secrets."platform-backup.env".path;
        StateDirectory = "platform-backup";
        CacheDirectory = "platform-backup";
        UMask = "0077";
      };
    };
    systemd.timers.platform-backup-maintenance = lib.mkIf (config.networking.hostName == "nas-01") {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "Sun *-*-* 03:00:00";
        Persistent = true;
        RandomizedDelaySec = "15m";
      };
    };

    # Weekly proof that both hosts' platform snapshots restore: a bounded
    # sample (databases, the homelab repository and CouchDB) is restored into
    # scratch space and verified; a throwaway loopback CouchDB opens the
    # restored copy. node exporter publishes the result for the RestoreDrill
    # alerts.
    systemd.services.platform-restore-drill = lib.mkIf (config.networking.hostName == "nas-01") {
      description = "Restore and verify a sample of the platform backups";
      after = [ "platform-backup-maintenance.service" ];
      # Activation must not run the drill inline: a failing or pending drill
      # would fail every nas-01 deploy and its rollback (2026-11-10). The
      # timer below reruns it after a deploy instead.
      restartIfChanged = false;
      unitConfig.RequiresMountsFor = "/persist";
      path = [
        pkgs.git
        pkgs.restic
      ];
      environment.PYTHONPATH = toString source;
      environment.XDG_CACHE_HOME = "/var/cache/platform-backup";
      serviceConfig = {
        Type = "oneshot";
        ExecStart =
          "${pkgs.python313}/bin/python -m scripts.ci.platform_backup --host ${config.networking.hostName} --mode drill"
          + lib.optionalString (cfg.couchdbDataPath != "") " --couchdb ${pkgs.couchdb3}/bin/couchdb";
        EnvironmentFile = config.sops.secrets."platform-backup.env".path;
        StateDirectory = "platform-backup";
        CacheDirectory = "platform-backup";
        UMask = "0077";
      };
    };
    systemd.timers.platform-restore-drill = lib.mkIf (config.networking.hostName == "nas-01") {
      wantedBy = [ "timers.target" ];
      # A deploy that changes the drill's code restarts the timer, so the new
      # code runs OnActiveSec later.
      restartTriggers = [ source ];
      timerConfig = {
        # Every two hours; the drill itself skips until a dataset failed, is
        # pending, or last passed six days ago, so failures retry quickly and
        # passes recur weekly without the unit ever ending up failed.
        OnCalendar = "*-*-* 00/2:30:00";
        # Also after boot and after a deploy changes the timer, so evidence
        # exists before RestoreDrillMissing fires and a rebuilt NAS proves
        # its backups straight away.
        OnActiveSec = "15min";
        Persistent = true;
        RandomizedDelaySec = "15m";
      };
    };
  };
}
