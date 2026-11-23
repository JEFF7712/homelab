{ ... }:
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

  # LAN-initiated traffic to a remote peer (CI deploys, backups to home-pc-01)
  # leaves wt0 with its LAN source. The receiving peer's WireGuard admits that
  # source only from whichever routing peer it picked for the LAN route, and
  # NetBird policies match peers, not LAN subnets, so it is dropped. Leaving
  # with this peer's own NetBird address makes such flows policy-addressable.
  networking.nftables.tables.netbird-lan-egress = {
    family = "ip";
    content = ''
      chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        oifname "wt0" ip saddr != 100.64.0.0/10 masquerade
      }
    '';
  };

  systemd.services.netbird = {
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
  };

  environment.persistence."/persist".directories = [
    "/var/lib/netbird"
  ];
}
