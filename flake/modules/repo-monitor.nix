{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.repoMonitor;

  specialMirrorArgs = lib.concatMapStringsSep " " (
    mirror:
    "--special-mirror "
    + lib.escapeShellArg (
      "${mirror.repository}:${mirror.githubRepository}:${mirror.defaultBranch}:${mirror.mode}"
    )
  ) cfg.specialMirrors;
in
{
  options.homelab.repoMonitor = {
    enable = lib.mkEnableOption "repository health monitor for Forgejo, GitHub mirrors, and Woodpecker CI";

    port = lib.mkOption {
      type = lib.types.port;
      default = 9110;
      description = "HTTP port exposing the Prometheus metrics endpoint.";
    };

    address = lib.mkOption {
      type = lib.types.str;
      default = "0.0.0.0";
      description = "Address readers use to reach the monitor; used for scrape configuration only.";
    };

    projectsPath = lib.mkOption {
      type = lib.types.path;
      default = ../../config/ci/projects.json;
      description = "Authoritative repository manifest that selects monitored repositories.";
    };

    applicationsPath = lib.mkOption {
      type = lib.types.path;
      default = ../../config/ci/applications.json;
      description = "Application catalog used to classify expected CI status contexts.";
    };

    environmentFile = lib.mkOption {
      type = lib.types.str;
      default = "/persist/repo-monitor.env";
      description = "Root-owned mode-0600 file providing FORGEJO_READ_TOKEN and GITHUB_TOKEN.";
    };

    pollIntervalSeconds = lib.mkOption {
      type = lib.types.int;
      default = 300;
      description = "Seconds between collection cycles.";
    };

    mirrorGraceSeconds = lib.mkOption {
      type = lib.types.int;
      default = 5400;
      description = "Allowed asynchronous Forgejo-to-GitHub mirror lag before reporting unhealthy.";
    };

    ciGraceSeconds = lib.mkOption {
      type = lib.types.int;
      default = 1800;
      description = "Grace period after a push before a missing CI validation is reported.";
    };

    ciStuckSeconds = lib.mkOption {
      type = lib.types.int;
      default = 5400;
      description = "Seconds a pending or running validation may stay open before it is reported stuck.";
    };

    ciLookbackSeconds = lib.mkOption {
      type = lib.types.int;
      default = 604800;
      description = "Age beyond which a commit without CI validation is treated as inactive, not missing.";
    };

    specialMirrors = lib.mkOption {
      type = lib.types.listOf (
        lib.types.submodule {
          options = {
            repository = lib.mkOption { type = lib.types.str; };
            githubRepository = lib.mkOption { type = lib.types.str; };
            defaultBranch = lib.mkOption {
              type = lib.types.str;
              default = "main";
            };
            mode = lib.mkOption {
              type = lib.types.enum [ "append" ];
              default = "append";
            };
          };
        }
      );
      default = [ ];
      description = ''
        Mirrors that do not use standard Forgejo push-mirror semantics. The
        `append` mode is for the CI-driven `homelab` mirror, where GitHub-only
        commits are expected and only ancestry is verified.
      '';
    };

    firewallSources = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "10.0.0.0/16"
        "10.42.0.0/16"
        "100.64.0.0/10"
      ];
      description = "CIDRs permitted to scrape the metrics endpoint.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.repo-monitor = {
      description = "Repository health monitor (Forgejo mirrors, GitHub, Woodpecker CI)";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      environment = {
        POLL_INTERVAL_SECONDS = toString cfg.pollIntervalSeconds;
        MIRROR_GRACE_SECONDS = toString cfg.mirrorGraceSeconds;
        CI_GRACE_SECONDS = toString cfg.ciGraceSeconds;
        CI_STUCK_SECONDS = toString cfg.ciStuckSeconds;
        CI_LOOKBACK_SECONDS = toString cfg.ciLookbackSeconds;
      };
      serviceConfig = {
        ExecStart =
          "${pkgs.python3}/bin/python3 ${./repo_monitor.py}"
          + " --projects ${cfg.projectsPath}"
          + " --applications ${cfg.applicationsPath}"
          + " --port ${toString cfg.port}"
          + lib.optionalString (cfg.specialMirrors != [ ]) " ${specialMirrorArgs}";
        EnvironmentFile = cfg.environmentFile;
        Restart = "always";
        RestartSec = "10s";
        DynamicUser = true;
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

    networking.firewall.extraInputRules = lib.mkIf (cfg.firewallSources != [ ]) ''
      ip saddr { ${lib.concatStringsSep ", " cfg.firewallSources} } tcp dport ${toString cfg.port} accept
    '';
  };
}
