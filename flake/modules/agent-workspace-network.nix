{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.agent-workspace-network;
  manifest = builtins.fromJSON (builtins.readFile cfg.manifestFile);
  recoveryManifest = cfg.recoveryManifest;
  enabledWorkspaces = builtins.filter (
    workspace: workspace.enabled && workspace.host == config.networking.hostName
  ) (manifest.workspaces ++ recoveryManifest.workspaces);
  safeInterface = value: builtins.match "[a-zA-Z0-9_.-]{1,15}" value != null;
  safeIPv4 = value: builtins.match "[0-9.]+" value != null;
  safeIPv4CIDR = value: builtins.match "[0-9.]+/[0-9]+" value != null;
  safeWorkspace =
    workspace:
    safeInterface workspace.network.bridge
    && safeInterface workspace.network.tap
    && safeInterface workspace.network.uplink
    && safeIPv4CIDR workspace.network.segment
    && safeIPv4CIDR workspace.network.address
    && safeIPv4 workspace.network.gateway
    && (workspace.network.ha_api == null || safeIPv4 workspace.network.ha_api)
    && builtins.all safeIPv4 workspace.network.dns
    && builtins.all safeIPv4 workspace.network.ntp
    && builtins.all safeIPv4CIDR workspace.network.ingress_sources;
  privateRanges = "{ 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.168.0.0/16, 198.18.0.0/15, 224.0.0.0/4, 240.0.0.0/4 }";
  workspaceInputRules = workspace: ''
    iifname "${workspace.network.bridge}" ether saddr != ${workspace.network.mac} counter drop
    iifname "${workspace.network.bridge}" ip6 saddr ::/0 counter drop
    iifname "${workspace.network.bridge}" ip saddr != ${builtins.head (lib.splitString "/" workspace.network.address)} counter drop
    iifname "${workspace.network.bridge}" ct state established,related accept
    iifname "${workspace.network.bridge}" counter drop
  '';
  workspaceRules = workspace: ''
    iifname "${workspace.network.bridge}" ether saddr != ${workspace.network.mac} counter drop
    iifname "${workspace.network.bridge}" ip6 saddr ::/0 counter drop
    iifname "${workspace.network.bridge}" ip saddr != ${builtins.head (lib.splitString "/" workspace.network.address)} counter drop
    iifname "${workspace.network.bridge}" ct state established,related accept
    ${lib.optionalString (workspace.network.ha_api != null)
      ''iifname "${workspace.network.bridge}" ip daddr ${workspace.network.ha_api} tcp dport 443 ct state new accept''
    }
    ${lib.optionalString (workspace.network.dns != [ ])
      ''
        iifname "${workspace.network.bridge}" ip daddr { ${lib.concatStringsSep ", " workspace.network.dns} } udp dport 53 ct state new accept
            iifname "${workspace.network.bridge}" ip daddr { ${lib.concatStringsSep ", " workspace.network.dns} } tcp dport 53 ct state new accept''
    }
    ${lib.optionalString (workspace.network.ntp != [ ])
      ''iifname "${workspace.network.bridge}" ip daddr { ${lib.concatStringsSep ", " workspace.network.ntp} } udp dport 123 ct state new accept''
    }
    iifname "${workspace.network.bridge}" ip daddr ${privateRanges} counter drop
    iifname "${workspace.network.bridge}" oifname "${workspace.network.uplink}" ct state new accept
    iifname "${workspace.network.bridge}" counter drop
    oifname "${workspace.network.bridge}" ct state established,related accept
    oifname "${workspace.network.bridge}" ip daddr ${builtins.head (lib.splitString "/" workspace.network.address)} ip saddr { ${lib.concatStringsSep ", " workspace.network.ingress_sources} } tcp dport { 22, 443 } ct state new accept
    oifname "${workspace.network.bridge}" counter drop
  '';
in
{
  options.services.agent-workspace-network = {
    enable = lib.mkEnableOption "host-owned isolated agent workspace networks";
    manifestFile = lib.mkOption {
      type = lib.types.path;
      default = ../../config/agent-workspaces/workspaces.json;
      description = "Validated agent workspace manifest used to derive bridges and firewall policy.";
    };
    recoveryManifest = lib.mkOption {
      type = lib.types.attrs;
      default = {
        schema_version = 1;
        workspaces = [ ];
      };
      description = "Optional network-only recovery definitions for existing guest disks; entries do not enroll workspaces.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = manifest.schema_version == 1;
        message = "Agent workspace network manifest schema_version must be 1.";
      }
      {
        assertion = recoveryManifest.schema_version == 1;
        message = "Agent workspace recovery network manifest schema_version must be 1.";
      }
      {
        assertion = builtins.all safeWorkspace enabledWorkspaces;
        message = "Agent workspace network fields contain unsafe values; run workspace-validate.";
      }
      {
        assertion = config.networking.useNetworkd;
        message = "Agent workspace networking requires networking.useNetworkd.";
      }
    ];

    boot.kernel.sysctl = {
      "net.ipv4.ip_forward" = 1;
      "net.ipv6.conf.all.forwarding" = 0;
    };

    systemd.network.netdevs = builtins.listToAttrs (
      map (workspace: {
        name = "40-${workspace.id}";
        value.netdevConfig = {
          Kind = "bridge";
          Name = workspace.network.bridge;
        };
      }) enabledWorkspaces
    );
    systemd.network.networks = builtins.listToAttrs (
      map (workspace: {
        name = "40-${workspace.id}";
        value = {
          matchConfig.Name = workspace.network.bridge;
          address = [
            "${workspace.network.gateway}/${builtins.elemAt (lib.splitString "/" workspace.network.segment) 1}"
          ];
          networkConfig = {
            DHCP = "no";
            IPv6AcceptRA = false;
            LinkLocalAddressing = "no";
            ConfigureWithoutCarrier = true;
          };
          linkConfig.RequiredForOnline = "no";
        };
      }) enabledWorkspaces
    );

    networking.nftables.tables.agent-workspaces = {
      family = "inet";
      content = ''
        chain input {
          type filter hook input priority -5; policy accept;
          ${lib.concatMapStringsSep "\n" workspaceInputRules enabledWorkspaces}
        }
        chain forward {
          type filter hook forward priority -5; policy accept;
          ct state invalid drop
          ${lib.concatMapStringsSep "\n" workspaceRules enabledWorkspaces}
        }
        chain postrouting {
          type nat hook postrouting priority srcnat; policy accept;
          ${lib.concatMapStringsSep "\n" (workspace: ''
            ip saddr ${workspace.network.segment} oifname "${workspace.network.uplink}" masquerade
          '') enabledWorkspaces}
        }
      '';
    };

    systemd.services.libvirtd = {
      requires = [
        "nftables.service"
        "systemd-networkd.service"
      ];
      after = [
        "nftables.service"
        "systemd-networkd.service"
      ];
    };
    systemd.services.nftables.preStop = lib.concatMapStringsSep "\n" (workspace: ''
      domain="agent-${workspace.id}"
      tap="${workspace.network.tap}"
      if ${pkgs.iproute2}/bin/ip link show dev "$tap" >/dev/null 2>&1; then
        ${pkgs.iproute2}/bin/ip link set dev "$tap" down
      fi
      ${pkgs.coreutils}/bin/timeout 10 ${config.virtualisation.libvirtd.package}/bin/virsh destroy "$domain" >/dev/null 2>&1 || true
    '') enabledWorkspaces;
  };
}
