{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.woodpecker;
in
{
  options.homelab.woodpecker = {
    enable = lib.mkEnableOption "Woodpecker CI server and tiered execution agents";

    serverHost = lib.mkOption {
      type = lib.types.str;
      default = "http://ci.internal:8000";
      description = "Canonical public URL for the Woodpecker web UI.";
    };

    forgejoUrl = lib.mkOption {
      type = lib.types.str;
      default = "http://git.internal:3000";
      description = "Forgejo instance URL.";
    };

    forgejoClientId = lib.mkOption {
      type = lib.types.str;
      default = "0b07d9b0-bd0a-4634-a0d3-a47f8c2229fb";
      description = "Forgejo OAuth2 Client ID.";
    };
  };

  config = lib.mkIf cfg.enable {
    # 1. Systemd slice for resource governance (32G RAM, 10 cores quota)
    systemd.slices."ci" = {
      description = "Woodpecker Continuous Integration Resource Slice";
      sliceConfig = {
        MemoryMax = "32G";
        CPUQuota = "1000%";
        CPUWeight = 50;
      };
    };

    # 2. Container runtime for sandbox agent
    virtualisation.docker = {
      enable = true;
      autoPrune = {
        enable = true;
        dates = "weekly";
      };
    };

    # 3. Dedicated system service users
    users.users.woodpecker = {
      isSystemUser = true;
      group = "woodpecker";
      home = "/persist/woodpecker";
      createHome = false;
    };
    users.groups.woodpecker = { };
    users.groups.woodpecker-agents = { };

    users.users.woodpecker-sandbox = {
      isSystemUser = true;
      group = "woodpecker-sandbox";
      extraGroups = [
        "docker"
        "woodpecker-agents"
      ];
      home = "/persist/woodpecker/workspace-sandbox";
      createHome = false;
    };
    users.groups.woodpecker-sandbox = { };

    users.users.woodpecker-ci = {
      isSystemUser = true;
      group = "woodpecker-ci";
      extraGroups = [ "woodpecker-agents" ];
      home = "/persist/woodpecker/workspace-trusted";
      createHome = false;
    };
    users.groups.woodpecker-ci = { };

    users.users.woodpecker-deploy = {
      isSystemUser = true;
      group = "woodpecker-deploy";
      extraGroups = [ "woodpecker-agents" ];
      home = "/persist/woodpecker/workspace-deploy";
      createHome = false;
    };
    users.groups.woodpecker-deploy = { };

    # Allow trusted Nix operations for woodpecker-ci and woodpecker-deploy
    nix.settings.trusted-users = [
      "woodpecker-ci"
      "woodpecker-deploy"
    ];

    # 4. Storage preparation
    systemd.tmpfiles.rules = [
      "d /persist/woodpecker 0755 root root -"
      "d /persist/woodpecker/server-data 0700 woodpecker woodpecker -"
      "d /persist/woodpecker/workspace-sandbox 0750 woodpecker-sandbox woodpecker-sandbox -"
      "d /persist/woodpecker/workspace-trusted 0750 woodpecker-ci woodpecker-ci -"
      "d /persist/woodpecker/workspace-deploy 0700 woodpecker-deploy woodpecker-deploy -"
    ];

    # 5. Woodpecker Server
    services.woodpecker-server = {
      enable = true;
      environment = {
        WOODPECKER_HOST = cfg.serverHost;
        WOODPECKER_SERVER_ADDR = ":8000";
        WOODPECKER_GRPC_ADDR = "0.0.0.0:9000";
        WOODPECKER_GITEA = "true";
        WOODPECKER_GITEA_URL = cfg.forgejoUrl;
        WOODPECKER_GITEA_CLIENT = cfg.forgejoClientId;
        WOODPECKER_OPEN = "true";
        WOODPECKER_ADMIN = "rupan";
        WOODPECKER_LOG_LEVEL = "info";
        WOODPECKER_DATABASE_DRIVER = "sqlite3";
        WOODPECKER_DATABASE_DATASOURCE = "/persist/woodpecker/server-data/woodpecker.sqlite";
      };
      environmentFile = [ "/persist/woodpecker/server.env" ];
    };

    systemd.services.woodpecker-server = {
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        DynamicUser = lib.mkForce false;
        User = "woodpecker";
        Group = "woodpecker";
        Slice = "ci.slice";
        ReadWritePaths = [ "/persist/woodpecker" ];
        WorkingDirectory = lib.mkForce "/persist/woodpecker";
      };
    };

    # 6. Woodpecker Agents (Tiered)
    services.woodpecker-agents.agents = {
      # Tier 1: Sandbox (Docker container executor for PRs & feature branches)
      sandbox = {
        enable = true;
        extraGroups = [ "docker" ];
        environment = {
          WOODPECKER_SERVER = "127.0.0.1:9000";
          WOODPECKER_BACKEND = "docker";
          DOCKER_HOST = "unix:///run/docker.sock";
          WOODPECKER_AGENT_LABELS = "tier=sandbox,type=docker,platform=linux/amd64";
          WOODPECKER_MAX_WORKFLOWS = "4";
          WOODPECKER_LOG_LEVEL = "info";
          WOODPECKER_HEALTHCHECK = "false";
        };
        environmentFile = [ "/persist/woodpecker/agent.env" ];
      };

      # Tier 2: Trusted (Local executor for main branch plans, tests, flake checks)
      trusted = {
        enable = true;
        environment = {
          WOODPECKER_SERVER = "127.0.0.1:9000";
          WOODPECKER_BACKEND = "local";
          WOODPECKER_AGENT_LABELS = "tier=trusted,type=local,platform=linux/amd64";
          WOODPECKER_MAX_WORKFLOWS = "2";
          WOODPECKER_LOG_LEVEL = "info";
          WOODPECKER_HEALTHCHECK = "false";
          HOME = "/persist/woodpecker/workspace-trusted";
        };
        environmentFile = [ "/persist/woodpecker/agent.env" ];
        path = with pkgs; [
          attic-client
          bash
          coreutils
          curl
          findutils
          gawk
          git
          git-lfs
          gnugrep
          gnused
          gnutar
          gzip
          jq
          just
          nix
          openssh
          opentofu
        ];
      };

      # Tier 3: Deploy (Local executor strictly dedicated to deployment events)
      deploy = {
        enable = true;
        environment = {
          WOODPECKER_SERVER = "127.0.0.1:9000";
          WOODPECKER_BACKEND = "local";
          WOODPECKER_AGENT_LABELS = "tier=deploy,type=local,platform=linux/amd64";
          WOODPECKER_MAX_WORKFLOWS = "1";
          WOODPECKER_LOG_LEVEL = "info";
          WOODPECKER_HEALTHCHECK = "false";
          HOME = "/persist/woodpecker/workspace-deploy";
        };
        environmentFile = [ "/persist/woodpecker/agent.env" ];
        path = with pkgs; [
          attic-client
          bash
          coreutils
          curl
          findutils
          gawk
          git
          git-lfs
          gnugrep
          gnused
          gnutar
          gzip
          jq
          just
          nix
          openssh
          opentofu
        ];
      };
    };

    # Agent systemd overrides for user execution & slices
    systemd.services.woodpecker-agent-sandbox.serviceConfig = {
      DynamicUser = lib.mkForce false;
      User = "woodpecker-sandbox";
      Group = "woodpecker-sandbox";
      Slice = "ci.slice";
      ReadWritePaths = [
        "/persist/woodpecker/workspace-sandbox"
        "/tmp"
      ];
      WorkingDirectory = "/persist/woodpecker/workspace-sandbox";
    };

    systemd.services.woodpecker-agent-trusted.serviceConfig = {
      DynamicUser = lib.mkForce false;
      User = "woodpecker-ci";
      Group = "woodpecker-ci";
      Slice = "ci.slice";
      ReadWritePaths = [
        "/persist/woodpecker/workspace-trusted"
        "/tmp"
      ];
      WorkingDirectory = "/persist/woodpecker/workspace-trusted";
      MemoryDenyWriteExecute = lib.mkForce false;
      SystemCallFilter = lib.mkForce [ ];
      ProtectSystem = lib.mkForce false;
      PrivateUsers = lib.mkForce false;
      NoNewPrivileges = lib.mkForce false;
      LockPersonality = lib.mkForce false;
    };

    systemd.services.woodpecker-agent-deploy.serviceConfig = {
      DynamicUser = lib.mkForce false;
      User = "woodpecker-deploy";
      Group = "woodpecker-deploy";
      Slice = "ci.slice";
      ReadWritePaths = [
        "/persist/woodpecker/workspace-deploy"
        "/tmp"
      ];
      WorkingDirectory = "/persist/woodpecker/workspace-deploy";
      MemoryDenyWriteExecute = lib.mkForce false;
      SystemCallFilter = lib.mkForce [ ];
      ProtectSystem = lib.mkForce false;
      PrivateUsers = lib.mkForce false;
      NoNewPrivileges = lib.mkForce false;
      LockPersonality = lib.mkForce false;
    };

    # 7. Firewall configuration: open port 8000 for LAN web UI access
    networking.firewall.allowedTCPPorts = [ 8000 ];

    # 8. Host resolution for CI jobs
    networking.hosts = {
      "192.168.1.1" = [ "OPNsense.internal" ];
      "10.0.30.20" = [ "registry.rupan.dev" ];
    };
  };
}
