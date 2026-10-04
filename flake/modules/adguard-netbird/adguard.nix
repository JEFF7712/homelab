{ pkgs, ... }:
{
  services.adguardhome = {
    enable = true;
    host = "10.0.30.10";
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
        bind_hosts = [ "10.0.30.10" ];
        port = 53;
        bootstrap_dns = [
          "9.9.9.9"
          "149.112.112.112"
        ];
        upstream_dns = [
          "[/homelab/]10.0.30.1"
          "https://dns.quad9.net/dns-query"
        ];
        fallback_dns = [ "1.1.1.1" ];
        protection_enabled = true;
      };
      dhcp.enabled = false;
      filtering = {
        enabled = true;
        filtering_enabled = true;
        filters_update_interval = 24;
        rewrites = [
          {
            domain = "OPNsense.internal";
            answer = "192.168.1.1";
            enabled = true;
          }
          {
            domain = "registry.rupan.dev";
            answer = "10.0.30.20";
            enabled = true;
          }
          {
            domain = "git.internal";
            answer = "10.0.30.20";
            enabled = true;
          }
          {
            domain = "s3.internal";
            answer = "10.0.30.20";
            enabled = true;
          }
          {
            domain = "ci.internal";
            answer = "10.0.30.14";
            enabled = true;
          }
        ];
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
