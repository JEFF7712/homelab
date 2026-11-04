{ pkgs, tofunix }:
let
  inherit (pkgs) lib;
  render = import ./lib.nix { inherit pkgs tofunix; };
  fleet = import ../inventory { inherit lib; };
  switches = import ./switches {
    inherit lib;
    inherit (fleet) networks;
  };
  opnsense = import ./opnsense { inherit pkgs tofunix; };
  portMap = import ./switches/documentation.nix {
    inherit lib;
    inventory = switches;
  };
  access = import ./cloudflare-access/inventory.nix;
  outputs = {
    "switches/inventory.json" = switches.runtime;
    "switches/main.tf.json" = render {
      provider = "switches";
      modules = [ ./switches/resources.nix ];
      extra.import = import ./switches/imports.nix;
    };
    "cloudflare/main.tf.json" = render {
      provider = "cloudflare";
      modules = [ ./cloudflare/resources.nix ];
      extra.check = import ./cloudflare/checks.nix;
    };
    "cloudflare-access/applications.tf.json" = {
      resource.cloudflare_zero_trust_access_application =
        accessConfig.resource.cloudflare_zero_trust_access_application;
    };
    "cloudflare-access/policies.tf.json" = {
      resource.cloudflare_zero_trust_access_policy =
        accessConfig.resource.cloudflare_zero_trust_access_policy;
    };
    "cloudflare-access/imports.tf.json".import = access.imports;
  };
  accessConfig = render {
    provider = "cloudflare";
    modules = [ ./cloudflare-access/resources.nix ];
  };
in
{
  inherit outputs;
  package = pkgs.runCommand "tofu-config" { } (
    ''
      mkdir -p "$out/opnsense" "$out/switches"
      cp -r ${opnsense.package}/. "$out/opnsense/"
      cp ${pkgs.writeText "switch-port-map.md" portMap} "$out/switches/port-map.md"
    ''
    + lib.concatStringsSep "\n" (
      lib.mapAttrsToList (path: value: ''
        mkdir -p "$out/$(dirname '${path}')"
        cp ${pkgs.writeText (builtins.baseNameOf path) (builtins.toJSON value)} "$out/${path}"
      '') outputs
    )
  );
}
