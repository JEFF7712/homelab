# Unbound records for the LAN gateway. config/lan-gateway.json owns the host
# list; each enabled name resolves on the LAN to the gateway VIP instead of the
# Cloudflare Tunnel. The reconciler never deletes overrides, so a host leaves
# the gateway by setting enabled = false, which also drops its router entry.
{ lib, ... }:
let
  gateway = lib.importJSON ../config/lan-gateway.json;
in
{
  dns = lib.imap0 (index: host: {
    inherit (host) name enabled;
    inherit (gateway) address;
    description = "LAN gateway: same-name HTTPS on the LAN without the Cloudflare Tunnel";
    unboundOrder = 100 + index;
  }) gateway.hosts;
}
