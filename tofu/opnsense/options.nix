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
  ipv4 = types.addCheck str (
    value:
    builtins.match "[0-9]{1,3}(\\.[0-9]{1,3}){3}" value != null
    && builtins.all (part: lib.toInt part <= 255) (lib.splitString "." value)
  );
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
      networks = option (
        types.attrsOf (
          types.submodule {
            options = {
              description = option str;
              parent = option str;
              tag = option (types.ints.between 1 4094);
              interfaceName = option str;
              gateway = option ipv4;
              cidr = option (types.strMatching "[0-9.]+/[0-9]{1,2}");
              dhcp = default (types.nullOr (
                types.submodule {
                  options = {
                    dns_servers = option (types.listOf ipv4);
                    pools = option strings;
                    subnetId = option str;
                  };
                }
              )) null;
            };
          }
        )
      );
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
        unique (map (device: device.address) devices)
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
        (alias.content == [ ]) != (alias.devices == [ ])
        && (alias.devices == [ ] || alias.type == "host")
        && builtins.all (name: builtins.hasAttr name cfg.devices) alias.devices
      ) (builtins.attrValues cfg.aliases);
      message = "Aliases must declare either content or valid host-device references.";
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
