{ lib, ... }:
let
  inventory = import ./inventory.nix;
in
{
  resource.cloudflare_zero_trust_access_application = lib.mapAttrs (
    _: app: inventory.applicationDefaults // app
  ) inventory.applications;
  resource.cloudflare_zero_trust_access_policy = lib.mapAttrs (
    _: policy:
    {
      account_id = inventory.account;
      lifecycle.prevent_destroy = true;
    }
    // policy
  ) inventory.policies;
}
