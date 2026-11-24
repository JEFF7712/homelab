# Outside-in uptime of the homelab's public services. home-pc-01 sits on a
# different network, so it sees what visitors see: DNS, Cloudflare, the tunnel
# and the backend together. Gatus keeps per-endpoint uptime history (status
# page on NetBird, port 8080) and exports Prometheus metrics, which the
# cluster scrapes through the home-pc-01-uptime relay on adguard-netbird-01
# for alerting (ExternalEndpointDown) and Grafana.
{ lib, ... }:
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
      endpoints = lib.concatLists (
        lib.mapAttrsToList (group: hosts: map (probe group) hosts) endpoints.groups
      );
    };
  };
  # NetBird only: homelab-admins devices for the status page, and
  # adguard-netbird-01 for the Prometheus relay.
  networking.firewall.interfaces.wt0.allowedTCPPorts = [ 8080 ];
}
