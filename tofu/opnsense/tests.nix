{ pkgs, tofunix }:
let
  inherit (pkgs) lib;
  rejects = value: !(builtins.tryEval (builtins.deepSeq value true)).success;
  inventory =
    module:
    (import ./. {
      inherit pkgs tofunix;
      extraModules = [ module ];
    }).terraform;
  provider =
    resource:
    ((import "${tofunix}/lib" { inherit pkgs; }).mkModule {
      moduleConfig = {
        imports = [ ./generated/provider.nix ];
        resource.opnsense_interfaces_vlan.probe = {
          description = "Probe VLAN";
          parent = "igb0";
          tag = 20;
        }
        // resource;
      };
    }).config.final;
  tests = {
    duplicateAddress = rejects (inventory {
      homelab.devices.laptop_nixos.address = lib.mkForce "10.0.20.124";
    });
    duplicateMAC = rejects (inventory {
      homelab.devices.laptop_nixos.mac = lib.mkForce "28:84:85:4d:92:e8";
    });
    duplicateVLAN = rejects (inventory {
      homelab.networks.clients.tag = lib.mkForce 10;
    });
    invalidAddress = rejects (inventory {
      homelab.devices.laptop_nixos.address = lib.mkForce "999.0.0.1";
    });
    unknownNetwork = rejects (inventory {
      homelab.devices.laptop_nixos.network = lib.mkForce "missing";
    });
    unknownAliasDevice = rejects (inventory {
      homelab.aliases.admin_laptop.devices = lib.mkForce [ "missing" ];
    });
    unknownProviderArgument = rejects (provider {
      typo_attribute = true;
    });
    wrongProviderType = rejects (provider {
      tag = "wrong";
    });
    validProviderReference =
      !rejects (provider {
        tag = "\${var.vlan_tag}";
      });
  };
in
assert lib.assertMsg (builtins.all (value: value) (
  builtins.attrValues tests
)) "OPNsense type regressions failed: ${builtins.toJSON tests}";
pkgs.writeText "opnsense-type-checks.json" (builtins.toJSON tests)
