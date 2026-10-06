{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.woodpecker;
  python = pkgs.python313.withPackages (p: [
    p.pyyaml
    p.cryptography
  ]);
  policySource = pkgs.runCommand "woodpecker-policy-source" { } ''
    mkdir -p $out/scripts/ci
    cp ${../../scripts/ci/policy.py} $out/scripts/ci/policy.py
    touch $out/scripts/__init__.py $out/scripts/ci/__init__.py
  '';
  agent = tier: count: {
    enable = true;
    extraGroups = [ "docker" ];
    environment = {
      WOODPECKER_SERVER = "127.0.0.1:9000";
      WOODPECKER_BACKEND = "docker";
      DOCKER_HOST = "unix:///run/docker.sock";
      WOODPECKER_AGENT_LABELS = "tier=${tier},type=docker,platform=linux/amd64";
      WOODPECKER_MAX_WORKFLOWS = toString count;
      WOODPECKER_HEALTHCHECK = "false";
      WOODPECKER_BACKEND_DOCKER_LIMIT_MEM = "8589934592";
      WOODPECKER_BACKEND_DOCKER_LIMIT_CPU_QUOTA = "200000";
    };
    environmentFile = [ "/persist/woodpecker/agent-${tier}.env" ];
  };
in
{
  options.homelab.woodpecker = {
    enable = lib.mkEnableOption "Woodpecker CI with server-owned container execution policy";
    serverHost = lib.mkOption {
      type = lib.types.str;
      default = "http://ci.internal:8000";
    };
    forgejoUrl = lib.mkOption {
      type = lib.types.str;
      default = "http://git.internal:3000";
    };
    forgejoClientId = lib.mkOption {
      type = lib.types.str;
      default = "0b07d9b0-bd0a-4634-a0d3-a47f8c2229fb";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.slices.ci.sliceConfig = {
      MemoryMax = "32G";
      CPUQuota = "1000%";
      CPUWeight = 50;
    };
    virtualisation.docker = {
      enable = true;
      daemon.settings."cgroup-parent" = "ci.slice";
      daemon.settings."data-root" = "/persist/docker";
      daemon.settings.dns = [ "10.0.30.10" ];
      autoPrune = {
        enable = true;
        dates = "weekly";
      };
    };
    users.users.woodpecker = {
      isSystemUser = true;
      group = "woodpecker";
      home = "/persist/woodpecker";
    };
    users.groups.woodpecker = { };
    systemd.tmpfiles.rules = [
      "d /persist/woodpecker 0750 woodpecker woodpecker -"
      "d /persist/woodpecker/server-data 0700 woodpecker woodpecker -"
      "z /persist/woodpecker/server.env 0600 root root -"
      "z /persist/woodpecker/clone.env 0600 root root -"
      "z /persist/woodpecker/agent.env 0600 root root -"
      "z /persist/woodpecker/agent-sandbox.env 0600 root root -"
      "z /persist/woodpecker/agent-trusted.env 0600 root root -"
      "z /persist/woodpecker/agent-deploy.env 0600 root root -"
      "z /persist/woodpecker/policy.env 0600 root root -"
    ];
    services.woodpecker-server = {
      enable = true;
      package = pkgs.woodpecker-server.overrideAttrs (old: {
        patches = (old.patches or [ ]) ++ [
          ../patches/woodpecker-deployment-policy.patch
          ../patches/woodpecker-clone-identity.patch
        ];
        postCheck = (old.postCheck or "") + ''
          go test ./server/forge/gitea -run '^TestNetrcCloneAccount$'
        '';
      });
      environment = {
        WOODPECKER_HOST = cfg.serverHost;
        WOODPECKER_SERVER_ADDR = ":8000";
        WOODPECKER_GRPC_ADDR = "127.0.0.1:9000";
        WOODPECKER_GITEA = "true";
        WOODPECKER_GITEA_URL = cfg.forgejoUrl;
        WOODPECKER_GITEA_CLIENT = cfg.forgejoClientId;
        WOODPECKER_OPEN = "false";
        WOODPECKER_STATUS_CONTEXT = "ci/woodpecker";
        WOODPECKER_STATUS_CONTEXT_FORMAT = "{{ .context }}/{{ .workflow }}";
        WOODPECKER_ADMIN = "rupan";
        WOODPECKER_DATABASE_DRIVER = "sqlite3";
        WOODPECKER_DATABASE_DATASOURCE = "/persist/woodpecker/server-data/woodpecker.sqlite";
        WOODPECKER_CONFIG_EXTENSION_ENDPOINT = "http://127.0.0.1:8010/config";
        WOODPECKER_CONFIG_EXTENSION_EXCLUSIVE = "true";
        WOODPECKER_CONFIG_EXTENSION_NETRC = "true";
        WOODPECKER_EXTENSIONS_ALLOWED_HOSTS = "loopback";
      };
      environmentFile = [
        "/persist/woodpecker/server.env"
        "/persist/woodpecker/clone.env"
      ];
    };
    systemd.services.woodpecker-server.serviceConfig = {
      DynamicUser = lib.mkForce false;
      User = "woodpecker";
      Group = "woodpecker";
      Slice = "ci.slice";
      ReadWritePaths = [ "/persist/woodpecker" ];
      WorkingDirectory = lib.mkForce "/persist/woodpecker";
    };
    systemd.services.woodpecker-policy = {
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      environment.PYTHONPATH = toString policySource;
      serviceConfig = {
        ExecStart = "${python}/bin/python -m scripts.ci.policy --catalog ${../../config/ci/operations.json} --public-key %d/signature-public.pem";
        LoadCredential = "signature-public.pem:/persist/woodpecker/signature-public.pem";
        EnvironmentFile = "/persist/woodpecker/policy.env";
        DynamicUser = true;
        Restart = "on-failure";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
      };
    };
    services.woodpecker-agents.agents = {
      sandbox = agent "sandbox" 2;
      trusted = agent "trusted" 1;
      deploy = agent "deploy" 1;
    };
    networking.firewall.allowedTCPPorts = [ 8000 ];
    networking.hosts = {
      "192.168.1.1" = [ "OPNsense.internal" ];
      # registry.rupan.dev is pinned in k3s-server.nix, which every
      # woodpecker host also enables, so it is not repeated here.
      "10.0.30.20" = [
        "git.internal"
        "s3.internal"
      ];
      "10.0.30.14" = [ "ci.internal" ];
    };
  };
}
