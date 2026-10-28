# This resource owns the zone's custom-rule entry point. Before the first
# apply, inspect the live entry point and import/preserve any existing rules.
# See docs/runbooks/cloudflare-tunnel.md (US-only personal apps).
data "cloudflare_zone" "rupan" {
  filter = {
    name    = "rupan.dev"
    account = { id = var.cloudflare_account_id }
  }
}

locals {
  # Explicit hostnames keep public websites, analytics, Git/CI, notification
  # publishers and automation receivers globally reachable.
  personal_app_hostnames = toset([
    "photos.rupan.dev",
    "obsidian.rupan.dev",
    "ha.rupan.dev",
    "ma.rupan.dev",
    "ledfx.rupan.dev",
    "grafana.rupan.dev",
    "jellyfin.rupan.dev",
    "navidrome.rupan.dev",
    "music.rupan.dev",
    "seerr.rupan.dev",
    "recommender.rupan.dev",
    "requests.rupan.dev",
    "lidarr.rupan.dev",
    "radarr.rupan.dev",
    "sonarr.rupan.dev",
    "prowlarr.rupan.dev",
    "bazarr.rupan.dev",
    "slskd.rupan.dev",
    "torrent.rupan.dev",
    "qbittorrent.rupan.dev",
    "books.rupan.dev",
    "media.rupan.dev",
    "bookshelf.rupan.dev",
    "pod.rupan.dev",
    "arcade.rupan.dev",
    "approvals.rupan.dev",
  ])

  personal_apps_waf_rule = {
    ref         = "personal_apps_us_only"
    description = "Personal homelab apps: block visitors outside the US"
    action      = "block"
    enabled     = true
    expression = join(" ", [
      "(http.host in {${join(" ", [for host in sort(tolist(local.personal_app_hostnames)) : jsonencode(host)])}})",
      "and (ip.src.country ne \"US\")",
      "and not (http.host eq \"ha.rupan.dev\" and starts_with(http.request.uri.path, \"/api/webhook/\"))",
    ])
  }
}

resource "cloudflare_ruleset" "personal_apps" {
  zone_id     = data.cloudflare_zone.rupan.id
  name        = "Homelab custom WAF rules"
  description = "Country restrictions for personal applications"
  kind        = "zone"
  phase       = "http_request_firewall_custom"
  rules       = [local.personal_apps_waf_rule]

  lifecycle {
    prevent_destroy = true
  }
}
