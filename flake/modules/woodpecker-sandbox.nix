{
  config,
  lib,
  pkgs,
  fleetInventory,
  ...
}:
let
  cfg = config.homelab.woodpeckerSandbox;
  inherit (fleetInventory) hosts networks;
  tunnelUser = "woodpecker-sandbox-tunnel";
  knownHosts = pkgs.writeText "woodpecker-sandbox-known-hosts" ''
    ${hosts.homelab-04.address} ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPy04yANSz9NFohTBmfsxRCmzvrdV5OR29xTxD43gtHK
  '';
in
{
  options.homelab.woodpeckerSandbox = {
    enable = lib.mkEnableOption "one additional isolated Woodpecker sandbox slot";
    acceptTunnel = lib.mkEnableOption "the restricted sandbox agent RPC tunnel";
    agentPackage = lib.mkOption {
      type = lib.types.package;
      readOnly = true;
      internal = true;
      # WOODPECKER_BACKEND_DOCKER_DEVICES adds agent-chosen host devices to
      # every job container. Step `devices` would need repository volume
      # trust, which the server-owned policy keeps cleared.
      default = pkgs.woodpecker-agent.overrideAttrs (old: {
        patches = (old.patches or [ ]) ++ [ ../patches/woodpecker-agent-devices.patch ];
        postCheck = (old.postCheck or "") + ''
          go test ./pipeline/backend/docker -run '^TestToHostConfigAgentDevices$'
        '';
      });
      description = "Woodpecker agent that can add host devices to job containers.";
    };
    kvmDevice = lib.mkOption {
      type = lib.types.str;
      readOnly = true;
      internal = true;
      # NixOS VM checks run under TCG without it: several times slower, and
      # niks3-cache-service missed its shell timeout in every cache-publish.
      default = "/dev/kvm:/dev/kvm";
    };
  };

  config = lib.mkMerge [
    (lib.mkIf cfg.acceptTunnel {
      users.groups.${tunnelUser} = { };
      users.users.${tunnelUser} = {
        isSystemUser = true;
        group = tunnelUser;
        shell = pkgs.bash;
        openssh.authorizedKeys.keys = [
          ''restrict,port-forwarding,permitopen="127.0.0.1:9000" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICOXsveFBegbj8mLms7oP1TqiqEk06FHeeYIzs29JTFs homelab-05-sandbox-tunnel''
        ];
      };
      services.openssh.extraConfig = ''
        Match User ${tunnelUser}
          AllowTcpForwarding local
          AllowStreamLocalForwarding no
          PermitOpen 127.0.0.1:9000
          PermitListen none
          PermitTTY no
          X11Forwarding no
          ForceCommand ${pkgs.coreutils}/bin/false
        Match all
      '';
    })
    (lib.mkIf cfg.enable {
      sops.secrets = {
        "woodpecker-sandbox-agent.env".restartUnits = [ "woodpecker-agent-sandbox.service" ];
        woodpecker-sandbox-ssh-key.restartUnits = [ "woodpecker-sandbox-tunnel.service" ];
      };
      # The kubelet reserves three CPUs for CI (homelab-05 default.nix);
      # the slice may burst to six while the host is idle, as on homelab-04,
      # and CPUWeight keeps pods ahead when they compete. At a hard three,
      # full gates ran CPU-bound (1,114 s throttled in a 445 s Python lane,
      # pipeline #19844) while the host sat at load 3 of 12 threads.
      systemd.slices.ci.sliceConfig = {
        CPUQuota = "600%";
        CPUWeight = 50;
        MemoryHigh = "8G";
        MemoryMax = "10G";
      };
      virtualisation.docker = {
        enable = true;
        daemon.settings = {
          cgroup-parent = "ci.slice";
          data-root = "/persist/docker";
          dns = [
            hosts.adguard-netbird-01.address
            networks.infrastructure.gateway
          ];
          dns-opts = [ "no-aaaa" ];
        };
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
      networking.nftables.tables.ci-container-egress = {
        family = "inet";
        content = ''
          chain forward {
            type filter hook forward priority filter - 10; policy accept;
            iifname { "docker0", "br-*" } ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } meta l4proto { tcp, udp } th dport { 111, 2049 } counter drop
            iifname { "docker0", "br-*" } ip daddr { ${hosts.homelab-04.address}, ${hosts.homelab-05.address} } tcp dport { 22, 19000 } counter drop
          }
        '';
      };
      systemd.services.woodpecker-sandbox-tunnel = {
        wantedBy = [ "multi-user.target" ];
        after = [ "network-online.target" ];
        wants = [ "network-online.target" ];
        serviceConfig = {
          Type = "exec";
          DynamicUser = true;
          LoadCredential = "ssh-key:${config.sops.secrets.woodpecker-sandbox-ssh-key.path}";
          ExecStart = "${pkgs.openssh}/bin/ssh -F /dev/null -N -T -i %d/ssh-key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=${knownHosts} -o GlobalKnownHostsFile=/dev/null -o ExitOnForwardFailure=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 127.0.0.1:19000:127.0.0.1:9000 ${tunnelUser}@${hosts.homelab-04.address}";
          Restart = "always";
          RestartSec = 5;
          NoNewPrivileges = true;
          PrivateTmp = true;
          ProtectHome = true;
          ProtectSystem = "strict";
        };
      };
      services.woodpecker-agents.agents.sandbox = {
        enable = true;
        package = cfg.agentPackage;
        extraGroups = [ "docker" ];
        environment = {
          WOODPECKER_SERVER = "127.0.0.1:19000";
          WOODPECKER_HOSTNAME = "homelab-05-sandbox";
          WOODPECKER_BACKEND = "docker";
          DOCKER_HOST = "unix:///run/docker.sock";
          WOODPECKER_AGENT_LABELS = "tier=sandbox,type=docker,platform=linux/amd64";
          WOODPECKER_MAX_WORKFLOWS = "1";
          WOODPECKER_HEALTHCHECK = "false";
          WOODPECKER_BACKEND_DOCKER_LIMIT_MEM = "8589934592";
          # The whole ci slice; one slot, so the job may use all of it.
          WOODPECKER_BACKEND_DOCKER_LIMIT_CPU_QUOTA = "600000";
          WOODPECKER_BACKEND_DOCKER_DEVICES = cfg.kvmDevice;
        };
        environmentFile = [ config.sops.secrets."woodpecker-sandbox-agent.env".path ];
      };
      systemd.services.woodpecker-agent-sandbox = {
        after = [ "woodpecker-sandbox-tunnel.service" ];
        requires = [ "woodpecker-sandbox-tunnel.service" ];
        serviceConfig.Restart = lib.mkForce "always";
      };
    })
  ];
}
