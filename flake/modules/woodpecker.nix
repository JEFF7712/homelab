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
    cp ${../../scripts/ci/applications.py} $out/scripts/ci/applications.py
    cp ${../../scripts/ci/development_projects.py} $out/scripts/ci/development_projects.py
    cp ${../../scripts/ci/static_site.py} $out/scripts/ci/static_site.py
    cp ${../../scripts/ci/static_release.py} $out/scripts/ci/static_release.py
    cp ${../../scripts/ci/python_service.py} $out/scripts/ci/python_service.py
    cp ${../../scripts/ci/dotnet_service.py} $out/scripts/ci/dotnet_service.py
    cp ${../../scripts/ci/node_service.py} $out/scripts/ci/node_service.py
    cp ${../../scripts/ci/nixos_config.py} $out/scripts/ci/nixos_config.py
    cp ${../../scripts/ci/firmware.py} $out/scripts/ci/firmware.py
    cp ${../../scripts/ci/dockerfile_supply.py} $out/scripts/ci/dockerfile_supply.py
    cp ${../../scripts/ci/container_release.py} $out/scripts/ci/container_release.py
    cp ${../../scripts/ci/node_release.py} $out/scripts/ci/node_release.py
    cp ${../../scripts/ci/dotnet_release.py} $out/scripts/ci/dotnet_release.py
    touch $out/scripts/__init__.py $out/scripts/ci/__init__.py
    PYTHONPATH=$out ${python}/bin/python -c ${lib.escapeShellArg ''
      from pathlib import Path
      from scripts.ci.applications import load_catalog, configuration

      applications = load_catalog(Path("${../../config/ci/applications.json}"))
      for app in applications:
          if app.woodpecker_repository_id is None:
              continue
          owner, name = app.repository.split("/")
          request = {
              "repo": {"id": app.woodpecker_repository_id, "owner": owner, "name": name},
              "pipeline": {
                  "event": "push",
                  "ref": "refs/heads/" + app.default_branch,
                  "commit": "0" * 40,
                  "number": 1,
                  "from_fork": False,
              },
          }
          generated = configuration(request, applications)
          if app.release_kind != "none":
              assert any(item["name"] == "application-release.yaml" for item in generated["configs"]), app.id

      from scripts.ci.development_projects import load_development_projects
      load_development_projects(Path("${../../config/ci/projects.json}"))
    ''}
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
      default = "https://git.rupan.dev";
    };
    forgejoClientId = lib.mkOption {
      type = lib.types.str;
      default = "0b07d9b0-bd0a-4634-a0d3-a47f8c2229fb";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.slices.ci.sliceConfig = {
      MemoryHigh = "18G";
      MemoryMax = "22G";
      CPUQuota = "1000%";
      CPUWeight = 50;
    };
    virtualisation.docker = {
      enable = true;
      daemon.settings."cgroup-parent" = "ci.slice";
      daemon.settings."data-root" = "/persist/docker";
      daemon.settings.dns = [ "10.0.30.10" ];
      # Without --all, system prune only drops dangling images, so tagged CI
      # build images accumulated to ~90G on the shared /persist disk.
      autoPrune = {
        enable = true;
        dates = "weekly";
        flags = [
          "--all"
          "--filter"
          "until=168h"
        ];
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
      "d /persist/woodpecker/manual-backups 0700 root root -"
      # Manual pre-enroll sqlite copies must not accumulate in /root, which
      # is tmpfs: 2.6G of them caused the 2026-10-09 homelab-04 DiskPressure
      # outage. Stage intentional copies under /persist/woodpecker/manual-backups.
      "r /root/woodpecker.sqlite.before-enroll-*"
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
          ../patches/woodpecker-forgejo-visibility.patch
        ];
        postCheck = (old.postCheck or "") + ''
          go test ./server/forge/gitea -run '^(TestNetrcCloneAccount|TestForgejoVisibilityReconciliation)$'
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
        WOODPECKER_DEFAULT_CLONE_PLUGIN = "registry.rupan.dev/upstream/docker.io/woodpeckerci/plugin-git@sha256:0f06b03ec33137b556c77538563f66c400339052d0c5199d4c3ad1a2a37e1964";
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
    environment.etc."projectctl/ci-policy.json".source = pkgs.writeText "projectctl-ci-policy.json" (
      builtins.toJSON {
        schema_version = 1;
        exclusive = true;
        contract_digest =
          "sha256:"
          + builtins.hashString "sha256" (
            builtins.readFile ../../scripts/ci/policy.py
            + "\n"
            + builtins.readFile ../../scripts/ci/development_projects.py
          );
        package_digest = "sha256:" + builtins.hashString "sha256" (toString policySource);
        catalog = builtins.fromJSON (builtins.readFile ../../config/ci/projects.json);
      }
    );
    systemd.services.woodpecker-policy = {
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      environment.PYTHONPATH = toString policySource;
      path = [ pkgs.git ];
      serviceConfig = {
        ExecStart = "${python}/bin/python -m scripts.ci.policy --catalog ${../../config/ci/operations.json} --applications ${../../config/ci/applications.json} --projects ${../../config/ci/projects.json} --public-key %d/signature-public.pem --mirror %S/woodpecker-policy/homelab.git";
        StateDirectory = "woodpecker-policy";
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
      # Two trusted slots: scheduled read-only work holds at most one (its
      # shared concurrency group), so pushes and operator plans keep the other.
      trusted = agent "trusted" 2;
      # Operations serialize per resource group, so a second deploy slot lets
      # unrelated mutations (a cache push and a Home Assistant apply) overlap.
      deploy = agent "deploy" 2;
    };
    systemd.services.woodpecker-agent-sandbox.serviceConfig.Restart = lib.mkForce "always";
    systemd.services.woodpecker-agent-trusted.serviceConfig.Restart = lib.mkForce "always";
    systemd.services.woodpecker-agent-deploy.serviceConfig.Restart = lib.mkForce "always";
    networking.firewall.allowedTCPPorts = [ 8000 ];
    # deploy-ci-host steps SSH to this host's own address. That traffic is
    # delivered locally from the step's Docker bridge with a container source
    # address, so the 10.0.30.0/24 SSH rule never matches it. Steps already
    # reach SSH on every other fleet host through the masquerade.
    networking.firewall.extraInputRules = ''
      iifname { "docker0", "br-*" } tcp dport 22 accept
    '';
    networking.hosts = {
      "192.168.1.1" = [ "OPNsense.internal" ];
      # registry.rupan.dev is pinned in k3s-server.nix, which every
      # woodpecker host also enables, so it is not repeated here.
      "10.0.30.20" = [
        "git.internal"
        "git.rupan.dev"
        "s3.internal"
      ];
      "10.0.30.14" = [ "ci.internal" ];
    };
  };
}
