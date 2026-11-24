{
  lib,
  pkgs,
  fleetInventory,
  ...
}:
let
  inherit (fleetInventory) hosts networks;
  # LAN hosts cannot open connections to remote NetBird peers: this routing
  # peer's NetBird-managed forward rules drop new LAN-to-mesh flows (connections
  # it originates itself pass). Each relay listens on this host's LAN address
  # and reconnects from here, so the peer sees this host's mesh address
  # (100.94.249.145) and a NetBird policy with it as the source admits the flow.
  relays = {
    home-pc-01-ssh = {
      port = 2201;
      target = "100.94.53.129:22";
      sources = [ hosts.homelab-04.address ];
    };
    # Gatus uptime metrics; Prometheus scrapes from the cluster nodes.
    home-pc-01-uptime = {
      port = 2202;
      target = "100.94.53.129:8080";
      sources = [ networks.infrastructure.cidr ];
    };
  };
in
{
  users.users.rupan.extraGroups = [ "netbird" ];

  services.netbird = {
    useRoutingFeatures = "server";
    clients.default = {
      port = 51820;
      name = "netbird";
      interface = "wt0";
      hardened = true;
      autoStart = true;
      login.enable = false;
    };
  };

  systemd.services = {
    netbird = {
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
    };
  }
  // lib.mapAttrs' (
    name: relay:
    lib.nameValuePair "netbird-relay-${name}" {
      description = "Relay LAN connections to ${relay.target} over NetBird";
      requires = [ "netbird-relay-${name}.socket" ];
      after = [
        "netbird-relay-${name}.socket"
        "netbird.service"
      ];
      serviceConfig = {
        ExecStart = "${pkgs.systemd}/lib/systemd/systemd-socket-proxyd --exit-idle-time=10min ${relay.target}";
        DynamicUser = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        NoNewPrivileges = true;
      };
    }
  ) relays;

  systemd.sockets = lib.mapAttrs' (
    name: relay:
    lib.nameValuePair "netbird-relay-${name}" {
      wantedBy = [ "sockets.target" ];
      listenStreams = [ "${hosts.adguard-netbird-01.address}:${toString relay.port}" ];
      socketConfig.FreeBind = true;
    }
  ) relays;

  networking.firewall.extraInputRules = lib.concatMapStringsSep "\n" (
    relay:
    "ip saddr { ${lib.concatStringsSep ", " relay.sources} } tcp dport ${toString relay.port} accept"
  ) (builtins.attrValues relays);

  environment.persistence."/persist".directories = [
    "/var/lib/netbird"
  ];
}
