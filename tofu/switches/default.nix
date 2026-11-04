{
  lib,
  networks,
  extraModules ? [ ],
}:
let
  inherit (lib) mkOption types;
  port = types.submodule {
    options = {
      enabled = mkOption { type = types.bool; };
      speed = mkOption {
        type = types.enum [
          "auto"
          "10Mbps-half"
          "10Mbps-full"
          "100Mbps-half"
          "100Mbps-full"
          "1000Mbps-full"
        ];
      };
      flow_control = mkOption { type = types.bool; };
      pvid = mkOption { type = types.ints.between 1 4094; };
    };
  };
  vlan = types.submodule {
    options = {
      name = mkOption { type = types.strMatching "[A-Za-z0-9_-]{0,10}"; };
      tagged_ports = mkOption { type = types.listOf types.ints.positive; };
      untagged_ports = mkOption { type = types.listOf types.ints.positive; };
    };
  };
  switch = types.submodule {
    options = {
      host = mkOption { type = types.nonEmptyStr; };
      mac = mkOption { type = types.strMatching "([0-9a-f]{2}:){5}[0-9a-f]{2}"; };
      model = mkOption { type = types.nonEmptyStr; };
      hardware = mkOption { type = types.nonEmptyStr; };
      firmware = mkOption { type = types.nonEmptyStr; };
      description = mkOption { type = types.nullOr (types.strMatching "[A-Za-z0-9][A-Za-z0-9-]{0,31}"); };
      ports = mkOption { type = types.attrsOf port; };
      vlans = mkOption { type = types.attrsOf vlan; };
    };
  };
  inventory =
    (lib.evalModules {
      modules = [
        {
          options = {
            schema_version = mkOption { type = types.enum [ 1 ]; };
            switches = mkOption { type = types.attrsOf switch; };
          };
        }
        (import ./inventory.nix { inherit networks; })
      ]
      ++ extraModules;
    }).config;
  validSwitch =
    cfg:
    let
      portNumbers = map lib.toInt (builtins.attrNames cfg.ports);
      validVLAN =
        key: group:
        let
          members = group.tagged_ports ++ group.untagged_ports;
        in
        builtins.match "[1-9][0-9]*" key != null
        && lib.toInt key <= 4094
        && builtins.length members == builtins.length (lib.unique members)
        && builtins.all (p: builtins.elem p portNumbers) members;
    in
    cfg.vlans ? "1"
    && builtins.length (builtins.attrNames cfg.vlans) <= 32
    && builtins.all (x: x) (lib.mapAttrsToList validVLAN cfg.vlans)
    && builtins.all (x: x) (
      lib.mapAttrsToList (
        key: p:
        let
          group =
            cfg.vlans.${toString p.pvid} or {
              tagged_ports = [ ];
              untagged_ports = [ ];
            };
        in
        builtins.elem (lib.toInt key) (group.tagged_ports ++ group.untagged_ports)
      ) cfg.ports
    );
in
assert lib.assertMsg (builtins.all validSwitch (
  builtins.attrValues inventory.switches
)) "Invalid switch VLAN membership or PVID";
inventory
