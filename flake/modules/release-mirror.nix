{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.releaseMirror;
in
{
  options.homelab.releaseMirror = {
    enable = lib.mkEnableOption "published Forgejo release synchronization to GitHub";
    repository = lib.mkOption {
      type = lib.types.strMatching "[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+";
      description = "Explicit repository to synchronize, with the same owner and name on both forges.";
    };
    minimumVersion = lib.mkOption {
      type = lib.types.strMatching "v?[0-9]+\\.[0-9]+\\.[0-9]+";
      description = "Oldest release version managed by the synchronization service.";
    };
    environmentFile = lib.mkOption {
      type = lib.types.str;
      default = "/persist/repo-monitor.env";
      description = "Root-owned mode-0600 file with FORGEJO_READ_TOKEN and a GITHUB_TOKEN allowed to write releases.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.github-release-mirror = {
      description = "Synchronize published Forgejo releases to GitHub";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart =
          "${pkgs.python3}/bin/python3 ${../../scripts/ci/release_mirror.py}"
          + " --repository ${lib.escapeShellArg cfg.repository}"
          + " --minimum-version ${lib.escapeShellArg cfg.minimumVersion}";
        EnvironmentFile = cfg.environmentFile;
        TimeoutStartSec = "15min";
        DynamicUser = true;
        UMask = "0077";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
        ];
      };
    };
    systemd.timers.github-release-mirror = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "*:0/5";
        RandomizedDelaySec = "30s";
        Persistent = true;
      };
    };
  };
}
