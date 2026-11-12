{ lib, ... }:
let
  hosts =
    (lib.evalModules {
      modules = [
        {
          options.hosts = lib.mkOption {
            type = lib.types.listOf (lib.types.strMatching "[a-z0-9-]+\\.rupan\\.dev");
          };
          config.hosts = import ./personal-apps.nix;
        }
      ];
    }).config.hosts;
  rule = {
    ref = "personal_apps_us_only";
    description = "Personal homelab apps: block visitors outside the US";
    action = "block";
    enabled = true;
    expression = lib.concatStringsSep " " [
      "(http.host in {${
        lib.concatMapStringsSep " " builtins.toJSON (builtins.sort builtins.lessThan hosts)
      }})"
      ''and (ip.src.country ne "US")''
      ''and not (http.host eq "ha.rupan.dev" and starts_with(http.request.uri.path, "/api/webhook/"))''
    ];
  };
in
assert lib.assertMsg (
  builtins.length hosts == builtins.length (lib.unique hosts)
) "Duplicate personal app hostname";
{
  # Decode at OpenTofu runtime: the ConfigMap remains the single ingress owner.
  locals = {
    config_map_path = "\${abspath(\"\${path.module}/../../gitops/cloudflare/ingress-config.yaml\")}";
    config_map = "\${yamldecode(file(local.config_map_path))}";
    tunnel = "\${yamldecode(local.config_map.data[\"config.yaml\"])}";
    ingress = "\${[for rule in local.tunnel.ingress : { hostname = try(rule.hostname, null), service = rule.service, path = try(rule.path, null) }]}";
    personal_app_hostnames = hosts;
    personal_apps_waf_rule = rule;
  };
  data.cloudflare_zone.rupan.filter = {
    name = "rupan.dev";
    account.id = "\${var.cloudflare_account_id}";
  };
  resource = {
    cloudflare_zero_trust_tunnel_cloudflared_config.homelab = {
      account_id = "\${var.cloudflare_account_id}";
      tunnel_id = "\${local.tunnel.tunnel}";
      config.ingress = "\${local.ingress}";
    };
    cloudflare_ruleset.personal_apps = {
      zone_id = "\${data.cloudflare_zone.rupan.id}";
      name = "Homelab custom WAF rules";
      description = "Country restrictions for personal applications";
      kind = "zone";
      phase = "http_request_firewall_custom";
      rules = [ rule ];
      lifecycle.prevent_destroy = true;
    };
  };
  output = {
    tunnel_id = {
      value = "\${local.tunnel.tunnel}";
      description = "Tunnel this stack owns the configuration for.";
    };
    config_version = {
      value = "\${cloudflare_zero_trust_tunnel_cloudflared_config.homelab.version}";
      description = "Version the edge reports; increments on every applied change.";
    };
  };
}
