{ pkgs, fleetInventory, ... }:
{
  services.adguardhome = {
    enable = true;
    host = fleetInventory.hosts.adguard-netbird-01.address;
    port = 3000;
    mutableSettings = false;
    settings = {
      users = [
        {
          name = "admin";
          password = "$2b$10$EY4fb1ANVYrI8ud2FQnJFOrnx5coVM3wwvZau1SOKoKNKdb9snryi";
        }
      ];
      dns = {
        bind_hosts = [ fleetInventory.hosts.adguard-netbird-01.address ];
        port = 53;
        bootstrap_dns = [
          "9.9.9.9"
          "149.112.112.112"
        ];
        upstream_dns = [
          "[/homelab/]${fleetInventory.networks.infrastructure.gateway}"
          "https://dns.quad9.net/dns-query"
        ];
        fallback_dns = [ "1.1.1.1" ];
        protection_enabled = true;
        # The default limits 20 queries/s per /24, so every server on
        # 10.0.30.0/24 shared one budget. Dropped queries stalled each lookup
        # in Docker's embedded resolver for 4s and failed ~15% of them under CI
        # load. Limit per client instead, and exempt the CI host, which NATs
        # every job container behind one address.
        ratelimit = 100;
        ratelimit_subnet_len_ipv4 = 32;
        ratelimit_whitelist = [ fleetInventory.hosts.homelab-04.address ];
      };
      dhcp.enabled = false;
      filtering = {
        enabled = true;
        filtering_enabled = true;
        filters_update_interval = 24;
        rewrites = fleetInventory.dns.adguardRewrites;
      };
      filters = [
        {
          enabled = true;
          id = 1;
          name = "AdGuard DNS filter";
          url = "https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt";
        }
        {
          enabled = true;
          id = 2;
          name = "OISD Big";
          url = "https://big.oisd.nl";
        }
        {
          enabled = true;
          id = 3;
          name = "AdGuard Mobile Ads filter";
          url = "https://adguardteam.github.io/HostlistsRegistry/assets/filter_11.txt";
        }
      ];
    };
  };

  systemd.services.adguardhome = {
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig.StateDirectoryMode = "0700";
  };

  systemd.tmpfiles.rules = [
    "d /persist/var/lib/private 0700 root root -"
    "d /persist/var/lib/private/AdGuardHome 0700 nobody nogroup -"
  ];

  environment.persistence."/persist".directories = [
    "/var/lib/private/AdGuardHome"
  ];
}
