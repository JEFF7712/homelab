{ config, lib, ... }:
let
  inherit (lib) mkOption types;
  cfg = config.homelab;
  str = types.nonEmptyStr;
  strings = types.listOf types.str;
  option = type: mkOption { inherit type; };
  default =
    type: value:
    mkOption {
      inherit type;
      default = value;
    };
  sharedTypes = import ../../inventory/types.nix { inherit lib; };
  ipv4 = sharedTypes.ipv4;
  endpoint = types.submodule {
    options = {
      net = option str;
      port = default types.str "";
    };
  };
  unique = values: builtins.length values == builtins.length (lib.unique values);
  networks = builtins.attrValues cfg.networks;
  devices = builtins.attrValues cfg.devices;
  rules = builtins.attrValues cfg.rules;
in
{
  options = {
    assertions = default (types.listOf (
      types.submodule {
        options = {
          assertion = option types.bool;
          message = option str;
        };
      }
    )) [ ];
    homelab = {
      networks = option (types.attrsOf sharedTypes.network);
      hosts = option (types.attrsOf sharedTypes.host);
      devices = option (
        types.attrsOf (
          types.submodule {
            options = {
              description = option str;
              hostname = option str;
              address = option ipv4;
              mac = option (types.strMatching "[0-9a-f]{2}(:[0-9a-f]{2}){5}");
              network = option str;
            };
          }
        )
      );
      aliases = option (
        types.attrsOf (
          types.submodule {
            options = {
              description = option str;
              type = option (
                types.enum [
                  "host"
                  "network"
                  "port"
                ]
              );
              enabled = default types.bool true;
              content = default strings [ ];
              devices = default strings [ ];
              hosts = default strings [ ];
            };
          }
        )
      );
      rules = option (
        types.attrsOf (
          types.submodule {
            options = {
              description = option str;
              enabled = default types.bool true;
              sequence = option types.ints.unsigned;
              interface = option (types.submodule { options.interface = option strings; });
              filter = option (
                types.submodule {
                  options = {
                    action = option (
                      types.enum [
                        "pass"
                        "block"
                        "reject"
                      ]
                    );
                    direction = option (
                      types.enum [
                        "in"
                        "out"
                      ]
                    );
                    ip_protocol = option (
                      types.enum [
                        "inet"
                        "inet6"
                        "inet46"
                      ]
                    );
                    protocol = option str;
                    quick = default types.bool true;
                    log = default types.bool false;
                    source = option endpoint;
                    destination = option endpoint;
                  };
                }
              );
            };
          }
        )
      );
      portForwards = option (
        types.attrsOf (
          types.submodule {
            options = {
              description = option str;
              enabled = default types.bool true;
              sequence = option types.ints.unsigned;
              interface = option strings;
              ip_protocol = option str;
              protocol = option str;
              log = default types.bool false;
              nat_reflection = option str;
              source = option (
                types.submodule {
                  options = {
                    net = option str;
                    port = default (types.nullOr types.str) null;
                  };
                }
              );
              destination = option endpoint;
              target = option (
                types.submodule {
                  options = {
                    ip = option ipv4;
                    port = option str;
                  };
                }
              );
            };
          }
        )
      );
    };
  };
  config.assertions = [
    {
      assertion = unique (map (network: "${network.parent}:${toString network.tag}") networks);
      message = "VLAN tags must be unique on each parent interface.";
    }
    {
      assertion = unique (map (network: network.interfaceName) networks);
      message = "OPNsense interface names must be unique.";
    }
    {
      assertion =
        unique (map (device: device.address) (devices ++ builtins.attrValues cfg.hosts))
        && unique (map (device: device.mac) devices)
        && unique (map (device: device.hostname) devices);
      message = "Devices must have unique addresses, MAC addresses, and hostnames.";
    }
    {
      assertion = builtins.all (
        device: builtins.hasAttr device.network cfg.networks && cfg.networks.${device.network}.dhcp != null
      ) devices;
      message = "Every device must reference a declared DHCP network.";
    }
    {
      assertion = builtins.all (
        alias:
        builtins.length (
          builtins.filter (values: values != [ ]) [
            alias.content
            alias.devices
            alias.hosts
          ]
        ) == 1
        && ((alias.devices == [ ] && alias.hosts == [ ]) || alias.type == "host")
        && builtins.all (name: builtins.hasAttr name cfg.devices) alias.devices
        && builtins.all (name: builtins.hasAttr name cfg.hosts) alias.hosts
      ) (builtins.attrValues cfg.aliases);
      message = "Aliases must declare exactly one of content, valid device references, or valid fleet-host references.";
    }
    {
      assertion = unique (
        lib.concatMap (
          rule: map (interface: "${interface}:${toString rule.sequence}") rule.interface.interface
        ) rules
      );
      message = "Firewall rule sequences must be unique on each interface.";
    }
  ];
}
