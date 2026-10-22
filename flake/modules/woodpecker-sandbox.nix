{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.woodpeckerSandbox;
  tunnelUser = "woodpecker-sandbox-tunnel";
  knownHosts = pkgs.writeText "woodpecker-sandbox-known-hosts" ''
    10.0.30.14 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPy04yANSz9NFohTBmfsxRCmzvrdV5OR29xTxD43gtHK
  '';
in
{
  options.homelab.woodpeckerSandbox = {
    enable = lib.mkEnableOption "one additional isolated Woodpecker sandbox slot";
    acceptTunnel = lib.mkEnableOption "the restricted sandbox agent RPC tunnel";
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
      systemd.slices.ci.sliceConfig = {
        CPUQuota = "300%";
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
            "10.0.30.10"
            "10.0.30.1"
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
            iifname { "docker0", "br-*" } ip daddr { 10.0.30.14, 10.0.30.15 } tcp dport { 22, 19000 } counter drop
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
          ExecStart = "${pkgs.openssh}/bin/ssh -F /dev/null -N -T -i %d/ssh-key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=${knownHosts} -o GlobalKnownHostsFile=/dev/null -o ExitOnForwardFailure=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 127.0.0.1:19000:127.0.0.1:9000 ${tunnelUser}@10.0.30.14";
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
          WOODPECKER_BACKEND_DOCKER_LIMIT_CPU_QUOTA = "200000";
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
