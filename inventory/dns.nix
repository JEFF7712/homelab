{
  dns = [
    {
      name = "registry.rupan.dev";
      coredns = {
        server = "registry";
        order = 1;
      };
      host = "nas-01";
      description = "Local container registry: public DNS returns Cloudflare tunnel addresses that fail large layers";
      unboundOrder = 1;
      adguardOrder = 2;
    }
    {
      name = "npm.rupan.dev";
      host = "nas-01";
      description = "Local npm pull-through cache on nas-01: keeps producer builds off the public registry";
      unboundOrder = 2;
      adguardOrder = 3;
    }
    {
      name = "grafana.homelab";
      address = "10.0.40.12";
      description = "Grafana load-balancer VIP";
      unboundOrder = 3;
    }
    {
      name = "ntfy.homelab";
      address = "10.0.40.12";
      description = "Adopted pre-existing override: ntfy load-balancer VIP, verified live 2026-10-06";
      unboundOrder = 4;
    }
    {
      name = "ci.internal";
      coredns = {
        server = "internal";
        order = 3;
      };
      host = "homelab-04";
      description = "Mirror of the AdGuard rewrite: Woodpecker CI. The secondary resolver has no overrides, so without this a resolved degradation drops Forgejo webhook deliveries with no retry.";
      unboundOrder = 5;
      adguardOrder = 7;
    }
    {
      name = "git.internal";
      coredns = {
        server = "internal";
        order = 1;
      };
      host = "nas-01";
      description = "Mirror of the AdGuard rewrite: Forgejo.";
      unboundOrder = 6;
      adguardOrder = 5;
    }
    {
      name = "s3.internal";
      coredns = {
        server = "internal";
        order = 2;
      };
      host = "nas-01";
      description = "Mirror of the AdGuard rewrite: S3 state and artifact backend.";
      unboundOrder = 7;
      adguardOrder = 6;
    }
    {
      name = "git.rupan.dev";
      coredns = {
        server = "registry";
        order = 2;
      };
      host = "nas-01";
      description = "Mirror of the AdGuard rewrite: Forgejo HTTPS. CI clones must resolve it even when the secondary resolver is in use.";
      unboundOrder = 8;
      adguardOrder = 4;
    }
    {
      name = "OPNsense.internal";
      address = "192.168.1.1";
      description = "Mirror of the AdGuard rewrite for OPNsense.internal.";
      unboundOrder = 9;
      adguardOrder = 1;
    }
    {
      name = "jellyfin.rupan.dev";
      address = "10.0.40.16";
      description = "Jellyfin LAN load-balancer VIP: keeps LAN playback direct instead of hairpinning through the Cloudflare Tunnel.";
      unboundOrder = 10;
      adguardOrder = 8;
    }
  ];
}
