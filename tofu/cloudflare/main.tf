# The tunnel's ingress rules are not written out here. They are decoded from
# the same ConfigMap the connector pods mount, so
# gitops/cloudflare/ingress-config.yaml stays the one authoritative list and
# this stack only decides when it reaches the edge. Editing the Zero Trust
# dashboard edits nothing that survives the next apply.

locals {
  config_map_path = abspath("${path.module}/../../gitops/cloudflare/ingress-config.yaml")
  config_map      = yamldecode(file(local.config_map_path))
  tunnel          = yamldecode(local.config_map.data["config.yaml"])

  # The catch-all carries no hostname; every earlier rule must.
  # path must pass through: without it a pathed rule (e.g. the sovereign
  # /api/inquiries receiver) collapses onto its bare-hostname sibling and the
  # first match shadows the other at the edge.
  ingress = [
    for rule in local.tunnel.ingress : {
      hostname = try(rule.hostname, null)
      service  = rule.service
      path     = try(rule.path, null)
    }
  ]
}

check "catch_all_is_last" {
  assert {
    condition = (
      local.ingress[length(local.ingress) - 1].service == "http_status:404" &&
      local.ingress[length(local.ingress) - 1].hostname == null
    )
    error_message = "the final ingress rule must be the hostname-less catch-all, otherwise unmatched hosts stop returning 404"
  }
}

check "only_the_catch_all_lacks_a_hostname" {
  assert {
    condition = length([
      for rule in local.ingress : rule.hostname if rule.hostname == null
    ]) == 1
    error_message = "a rule other than the trailing catch-all has no hostname, so Cloudflare would match it as a catch-all and shadow every later rule"
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "homelab" {
  account_id = var.cloudflare_account_id
  tunnel_id  = local.tunnel.tunnel

  config = {
    ingress = local.ingress
  }
}

output "tunnel_id" {
  value       = local.tunnel.tunnel
  description = "Tunnel this stack owns the configuration for."
}

output "config_version" {
  value       = cloudflare_zero_trust_tunnel_cloudflared_config.homelab.version
  description = "Version the edge reports; increments on every applied change."
}
