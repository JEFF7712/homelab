{
  staticRoutes = [
    {
      network = "10.0.40.16/32";
      gatewayName = "GW_HOMELAB_04";
      gatewayHost = "homelab-04";
      gatewayInterface = "opt3";
      description = "Jellyfin LAN VIP return path via its pinned k3s node; replaces the manual FIB entry.";
    }
    {
      network = "100.64.0.0/10";
      gatewayName = "GW_ADGUARD_NETBIRD";
      gatewayHost = "adguard-netbird-01";
      gatewayInterface = "opt3";
      description = "NetBird mesh return path: adguard-netbird-01 routes the tailnet into the LAN.";
    }
  ];
}
