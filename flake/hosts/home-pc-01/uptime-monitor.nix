# Outside-in uptime of the homelab's public services. home-pc-01 sits on a
# different network, so it sees what visitors see: DNS, Cloudflare, the tunnel
# and the backend together.
#
# Two Gatus instances read config/uptime/external-endpoints.json:
# - private (8080, NetBird only): every hostname, Prometheus metrics, scraped
#   through the home-pc-01-uptime relay for alerting and Grafana;
# - public (8081): only the `public` hosts, no metrics, served at
#   uptime.rupan.dev through the home-pc-01-status relay and the tunnel.
{ lib, pkgs, ... }:
let
  endpoints = builtins.fromJSON (builtins.readFile ../../../config/uptime/external-endpoints.json);
  probe = group: host: {
    name = host;
    inherit group;
    url = "https://${host}/";
    interval = "2m";
    client.timeout = "15s";
    conditions = [
      # Access-gated sites answer with a login redirect; reaching it proves
      # the path. Tunnel or backend failures surface as 5xx (502, 530, 1033).
      "[STATUS] < 500"
      "[RESPONSE_TIME] < 10000"
      "[CERTIFICATE_EXPIRATION] > 72h"
    ];
  };
  allProbes = lib.concatLists (
    lib.mapAttrsToList (group: hosts: map (probe group) hosts) endpoints.groups
  );
  publicProbes = builtins.filter (e: builtins.elem e.name endpoints.public) allProbes;
  publicConfig = (pkgs.formats.yaml { }).generate "gatus-public.yaml" {
    web.port = 8081;
    metrics = false;
    storage = {
      type = "sqlite";
      path = "/var/lib/gatus-public/data.db";
    };
    ui = {
      title = "Rupan's services: status";
      header = "Service status";
      description = "Uptime of rupan.dev sites and apps, measured from outside the homelab.";
    };
    endpoints = publicProbes;
  };
in
{
  services.gatus = {
    enable = true;
    settings = {
      web.port = 8080;
      metrics = true;
      storage = {
        type = "sqlite";
        path = "/var/lib/gatus/data.db";
      };
      ui = {
        title = "Homelab uptime";
        header = "Homelab uptime (from home-pc-01)";
      };
      endpoints = allProbes;
    };
  };

  systemd.services.gatus-public = {
    description = "Gatus public status page (uptime.rupan.dev)";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    environment.GATUS_CONFIG_PATH = publicConfig;
    serviceConfig = {
      ExecStart = lib.getExe pkgs.gatus;
      DynamicUser = true;
      StateDirectory = "gatus-public";
      Restart = "on-failure";
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      NoNewPrivileges = true;
    };
  };

  # NetBird only: homelab-admins devices for the private page, and
  # adguard-netbird-01 for the metrics (8080) and public page (8081) relays.
  networking.firewall.interfaces.wt0.allowedTCPPorts = [
    8080
    8081
  ];
}
