{
  pkgs,
  tofunix,
  extraModules ? [ ],
}:
let
  inherit (pkgs) lib;
  evaluated = lib.evalModules {
    modules = [
      ./options.nix
      ./inventory.nix
    ]
    ++ extraModules;
  };
  inventory = lib.asserts.checkAssertWarn evaluated.config.assertions [ ] evaluated.config.homelab;
  tofunixLib = import "${tofunix}/lib" { inherit pkgs; };
  terraform =
    builtins.removeAttrs
      (tofunixLib.mkModule {
        moduleConfig = {
          imports = [
            ./generated/provider.nix
            ./resources.nix
          ];
          _module.args = { inherit inventory; };
        };
      }).config.final
      [ "terraform" ];
  assignments = map (network: {
    descr = network.interfaceName;
    dhcp6-ia-pd-len = "0";
    disablevlanhwfilter = "0";
    enable = "1";
    ipaddr = "${network.gateway}/${builtins.elemAt (lib.splitString "/" network.cidr) 1}";
    lock = "1";
    inherit (network) parent tag;
    type4 = "staticv4";
    type6 = "none";
  }) (builtins.sort (a: b: a.tag < b.tag) (builtins.attrValues inventory.networks));
in
{
  inherit inventory terraform assignments;
  package =
    pkgs.runCommand "opnsense-config"
      {
        terraformJSON = builtins.toJSON terraform;
        assignmentsJSON = builtins.toJSON assignments;
        inventoryJSON = builtins.toJSON inventory;
        passAsFile = [
          "terraformJSON"
          "assignmentsJSON"
          "inventoryJSON"
        ];
        nativeBuildInputs = [ pkgs.python3 ];
      }
      ''
        mkdir -p "$out"
        cp "$terraformJSONPath" "$out/main.tf.json"
        cp "$assignmentsJSONPath" "$out/assignments.json"
        cp "$inventoryJSONPath" "$out/inventory.json"
        python ${../../scripts/opnsense_config.py} --rendered "$out" --validate-only
      '';
}
