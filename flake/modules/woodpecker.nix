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
  # Publish operations reach this host only through this forced command, so
  # their key runs the deployed worker and nothing else, never root.
  serviceReleaseSource = pkgs.runCommand "service-release-source" { } ''
    mkdir -p $out/scripts/ci
    cp ${../../scripts/ci/service_release.py} $out/scripts/ci/service_release.py
    touch $out/scripts/__init__.py $out/scripts/ci/__init__.py
  '';
  serviceReleaseWorker = pkgs.writeShellScript "service-release-worker" ''
    export PATH=/run/current-system/sw/bin
    cd /persist/service-release-ci
    exec env PYTHONPATH=${serviceReleaseSource} ${pkgs.python3}/bin/python3 -m scripts.ci.service_release worker
  '';
  # Deploy-tier jobs take their catalog group's lock from this service
  # (scripts/ci/oplock.py); only their Docker network may reach it.
  oplockSource = pkgs.runCommand "woodpecker-oplock-source" { } ''
    mkdir -p $out/scripts/ci
    cp ${../../scripts/ci/oplock.py} $out/scripts/ci/oplock.py
    touch $out/scripts/__init__.py $out/scripts/ci/__init__.py
  '';
  deployNetwork = "ci-deploy";
  deployBridge = "br-cideploy";
  oplockPort = 8031;
  policySource = pkgs.runCommand "woodpecker-policy-source" { } ''
    mkdir -p $out/scripts/ci $out/scripts/verifier $out/config/ci
    cp ${../../scripts/verifier/enforcement.py} $out/scripts/verifier/enforcement.py
    cp ${../../config/ci/projectctl-contracts.json} $out/config/ci/projectctl-contracts.json
    cp ${../../scripts/ci/policy.py} $out/scripts/ci/policy.py
    cp ${../../scripts/ci/deployment_source.py} $out/scripts/ci/deployment_source.py
    cp ${../../config/ci/ship-routes.json} $out/config/ci/ship-routes.json
    cp ${../../scripts/ci/deployment_risk.py} $out/scripts/ci/deployment_risk.py
    PYTHONPATH=$out ${python}/bin/python -m scripts.ci.deployment_risk ${../..} > $out/authority-baseline.json
    cp ${../../scripts/ci/applications.py} $out/scripts/ci/applications.py
    cp ${../../scripts/ci/build_users.py} $out/scripts/ci/build_users.py
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
    touch $out/scripts/__init__.py $out/scripts/ci/__init__.py $out/scripts/verifier/__init__.py
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
  agent = tier: count: cpus: {
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
      WOODPECKER_BACKEND_DOCKER_LIMIT_CPU_QUOTA = toString (cpus * 100000);
    };
    environmentFile = [ config.sops.secrets."woodpecker-agent-${tier}.env".path ];
  };
in
{
  imports = [ ./woodpecker-sandbox.nix ];

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
    homelab.woodpeckerSandbox.acceptTunnel = true;
    # 22G on a 32G host with agent VMs and GPU pods hit MemoryMax millions
    # of times and swapped. The kubelet reserves typical CI use (8G); the
    # rest of this cap overlaps pod memory only when both peak.
    systemd.slices.ci.sliceConfig = {
      MemoryHigh = "10G";
      MemoryMax = "12G";
      CPUQuota = "1000%";
      CPUWeight = 50;
    };
    virtualisation.docker = {
      enable = true;
      daemon.settings."cgroup-parent" = "ci.slice";
      daemon.settings."data-root" = "/persist/docker";
      # Every other host keeps OPNsense as the second resolver; with AdGuard
      # alone, an AdGuard outage took name resolution from every CI job.
      daemon.settings.dns = [
        "10.0.30.10"
        "10.0.30.1"
      ];
      # Containers have no IPv6, so AAAA answers are never used. Under
      # concurrent lookups the parallel AAAA query is intermittently lost and
      # glibc waits out a 4s timeout per lookup; A-only lookups never stall.
      daemon.settings."dns-opts" = [ "no-aaaa" ];
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
    sops.secrets =
      lib.listToAttrs (
        map (
          tier:
          lib.nameValuePair "woodpecker-agent-${tier}.env" {
            restartUnits = [ "woodpecker-agent-${tier}.service" ];
          }
        ) (builtins.attrNames config.services.woodpecker-agents.agents)
      )
      // {
        "woodpecker-server.env".restartUnits = [ "woodpecker-server.service" ];
        "woodpecker-clone.env" = {
          sopsFile = ../../secrets/woodpecker-clone.sops.env;
          format = "dotenv";
          key = "";
          restartUnits = [ "woodpecker-server.service" ];
        };
        "woodpecker-policy.env".restartUnits = [ "woodpecker-policy.service" ];
      };
    users.users.woodpecker = {
      isSystemUser = true;
      group = "woodpecker";
      home = "/persist/woodpecker";
    };
    users.groups.woodpecker = { };
    users.users.service-release = {
      isSystemUser = true;
      group = "service-release";
      extraGroups = [ "docker" ];
      home = "/persist/service-release-ci";
      shell = pkgs.bash;
      openssh.authorizedKeys.keys = [
        "restrict,command=\"${serviceReleaseWorker}\" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBwyZ04cW036w14eDJ4QHjRrVxYfp0bvSzVumZdVWK8C service-release-ci"
      ];
    };
    users.groups.service-release = { };
    systemd.tmpfiles.rules = [
      "d /persist/woodpecker 0750 woodpecker woodpecker -"
      "d /persist/woodpecker/server-data 0700 woodpecker woodpecker -"
      "d /persist/woodpecker/manual-backups 0700 root root -"
      # Manual pre-enroll sqlite copies must not accumulate in /root, which
      # is tmpfs: 2.6G of them caused the 2026-10-09 homelab-04 DiskPressure
      # outage. Stage intentional copies under /persist/woodpecker/manual-backups.
      "r /root/woodpecker.sqlite.before-enroll-*"
      "d /persist/service-release-ci 0700 service-release service-release -"
    ];
    services.woodpecker-server = {
      enable = true;
      package = pkgs.woodpecker-server.overrideAttrs (old: {
        patches = (old.patches or [ ]) ++ [
          ../patches/woodpecker-deployment-policy.patch
          ../patches/woodpecker-clone-identity.patch
          ../patches/woodpecker-forgejo-visibility.patch
          ../patches/woodpecker-clone-retry.patch
        ];
        postPatch = (old.postPatch or "") + ''
          cp ${./woodpecker-clone-retry.sh} pipeline/frontend/yaml/compiler/homelab_clone_retry.sh
        '';
        postCheck = (old.postCheck or "") + ''
          go test ./server/forge/gitea -run '^(TestNetrcCloneAccount|TestForgejoVisibilityReconciliation)$'
          go test ./pipeline/frontend/yaml/compiler -run '^TestHomelabCloneRetry$'
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
        WOODPECKER_DEFAULT_CLONE_PLUGIN = "registry.rupan.dev/upstream/docker.io/woodpeckerci/plugin-git@sha256:b8d2401997be68ec25d62bd753fe5d30236e029fe007a5e823177e0e485ae205";
        WOODPECKER_EXTENSIONS_ALLOWED_HOSTS = "loopback";
      };
      environmentFile = [
        config.sops.secrets."woodpecker-server.env".path
        config.sops.secrets."woodpecker-clone.env".path
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
        contract_digest = "sha256:" + builtins.hashFile "sha256" ../../config/ci/projectctl-contracts.json;
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
        ExecStart = "${python}/bin/python -m scripts.ci.policy --catalog ${../../config/ci/operations.json} --applications ${../../config/ci/applications.json} --projects ${../../config/ci/projects.json} --public-key %d/signature-public.pem --mirror %S/woodpecker-policy/homelab.git --authority-baseline ${policySource}/authority-baseline.json";
        StateDirectory = "woodpecker-policy";
        LoadCredential = "signature-public.pem:/persist/woodpecker/signature-public.pem";
        EnvironmentFile = config.sops.secrets."woodpecker-policy.env".path;
        DynamicUser = true;
        Restart = "on-failure";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
      };
    };
    services.woodpecker-agents.agents = {
      # One sandbox slot: a second 8G validation overcommitted the host's
      # memory; homelab-05 carries the other sandbox slot. Four CPUs: at two,
      # parallel gate lanes spent more time throttled than running (#12452:
      # 1,098 s throttled in a 348 s gate). The ci slice caps all slots at
      # ten CPUs and CPUWeight keeps pods ahead under contention.
      sandbox = agent "sandbox" 1 4;
      # Two trusted slots: scheduled read-only work holds at most one (its
      # shared concurrency group), so pushes and operator plans keep the other.
      trusted = agent "trusted" 2 2;
      # Operations serialize per resource group, so a second deploy slot lets
      # unrelated mutations (a cache push and a Home Assistant apply) overlap.
      # Its containers also join the deploy network, the only one that
      # reaches the operation lock service.
      deploy = lib.recursiveUpdate (agent "deploy" 2 2) {
        environment.WOODPECKER_BACKEND_DOCKER_NETWORK = deployNetwork;
      };
    };
    systemd.services.woodpecker-agent-sandbox.serviceConfig.Restart = lib.mkForce "always";
    systemd.services.woodpecker-agent-trusted.serviceConfig.Restart = lib.mkForce "always";
    systemd.services.woodpecker-agent-deploy = {
      serviceConfig.Restart = lib.mkForce "always";
      requires = [ "ci-deploy-network.service" ];
      after = [ "ci-deploy-network.service" ];
    };
    systemd.services.ci-deploy-network = {
      description = "Docker network for deploy-tier CI containers";
      requires = [ "docker.service" ];
      after = [ "docker.service" ];
      path = [ config.virtualisation.docker.package ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = ''
        docker network inspect ${deployNetwork} >/dev/null 2>&1 || docker network create \
          --driver bridge --subnet 10.255.31.0/24 --gateway 10.255.31.1 \
          -o com.docker.network.bridge.name=${deployBridge} ${deployNetwork}
      '';
    };
    # Woodpecker cannot limit a catalog group to one running operation, so
    # operations hold the group's lock here for the life of a connection.
    systemd.services.woodpecker-oplock = {
      description = "Per-group locks for CI operations";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      environment.PYTHONPATH = toString oplockSource;
      serviceConfig = {
        ExecStart = "${python}/bin/python -m scripts.ci.oplock --port ${toString oplockPort}";
        DynamicUser = true;
        Restart = "always";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
      };
    };
    networking.firewall.allowedTCPPorts = [ 8000 ];
    # deploy-ci-host steps SSH to this host's own address. That traffic is
    # delivered locally from the step's Docker bridge with a container source
    # address, so the 10.0.30.0/24 SSH rule never matches it. Steps already
    # reach SSH on every other fleet host through the masquerade.
    # Only deploy-tier containers may take operation locks, so sandbox jobs
    # running pull request code cannot hold one to stall deployments.
    networking.firewall.extraInputRules = ''
      iifname { "docker0", "br-*" } tcp dport 22 accept
      iifname "${deployBridge}" tcp dport ${toString oplockPort} accept
    '';
    # CI containers masquerade as this host, which every NFS export trusts
    # with AUTH_SYS and no_root_squash. No job needs NFS, so a sandbox step
    # cannot read or write cluster volumes as root.
    networking.nftables.tables.ci-container-egress = {
      family = "inet";
      content = ''
        chain forward {
          type filter hook forward priority filter - 10; policy accept;
          iifname { "docker0", "br-*" } ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } meta l4proto { tcp, udp } th dport { 111, 2049 } counter drop
        }
      '';
    };
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
