# Only consumed resource schemas are checked in. Extend this list before using
# another resource, and regenerate with just tofu-provider-options.
{
  cloudflare = {
    name = "cloudflare";
    source = "cloudflare/cloudflare";
    address = "registry.opentofu.org/cloudflare/cloudflare";
    version = "5.28.0";
    resources = [
      "cloudflare_zero_trust_tunnel_cloudflared_config"
      "cloudflare_ruleset"
      "cloudflare_zero_trust_access_application"
      "cloudflare_zero_trust_access_policy"
    ];
    data = [ "cloudflare_zone" ];
  };
  switches = {
    name = "tplinkeasysmart";
    source = "registry.terraform.io/davidlukac/tplinkeasysmart";
    address = "registry.terraform.io/davidlukac/tplinkeasysmart";
    version = "0.32.7";
    resources = [
      "tplinkeasysmart_vlan_mode"
      "tplinkeasysmart_system"
      "tplinkeasysmart_port"
      "tplinkeasysmart_vlan_8021q_group"
      "tplinkeasysmart_vlan_8021q_pvid"
    ];
    data = [ "tplinkeasysmart_system_info" ];
  };
}
