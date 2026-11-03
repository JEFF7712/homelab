{ lib }:
let
  inherit (lib) types mkOption;
  option = type: mkOption { inherit type; };
  ipv4 = types.addCheck types.nonEmptyStr (
    value:
    builtins.match "[0-9]{1,3}(\\.[0-9]{1,3}){3}" value != null
    && builtins.all (part: lib.toInt part <= 255) (lib.splitString "." value)
  );
  cidr = types.addCheck (types.strMatching "[0-9.]+/[0-9]{1,2}") (
    value:
    let
      parts = lib.splitString "/" value;
    in
    ipv4.check (builtins.head parts) && lib.toInt (builtins.elemAt parts 1) <= 32
  );
  network = types.submodule {
    options = {
      description = option types.nonEmptyStr;
      parent = option types.nonEmptyStr;
      tag = option (types.ints.between 1 4094);
      interfaceName = option types.nonEmptyStr;
      gateway = option ipv4;
      cidr = option cidr;
      dhcp = mkOption {
        default = null;
        type = types.nullOr (
          types.submodule {
            options = {
              dns_servers = option (types.listOf ipv4);
              pools = option (types.listOf types.str);
              subnetId = option types.nonEmptyStr;
            };
          }
        );
      };
    };
  };
  host = types.submodule {
    options = {
      deploymentType = option (
        types.enum [
          "k3s-worker"
          "k3s-stateful"
          "appliance-dns"
          "appliance-nas"
        ]
      );
      deploymentOrder = option types.ints.positive;
      address = option ipv4;
      network = option types.nonEmptyStr;
      k3s = mkOption {
        type = types.bool;
        default = false;
      };
    };
  };
  dnsRecord = types.submodule {
    options = {
      name = option (
        types.addCheck types.nonEmptyStr (
          value:
          builtins.stringLength value <= 253
          && builtins.length (lib.splitString "." value) >= 2
          && builtins.all (
            label:
            builtins.stringLength label <= 63
            && builtins.match "[A-Za-z0-9]([-A-Za-z0-9]*[A-Za-z0-9])?" label != null
          ) (lib.splitString "." value)
        )
      );
      host = mkOption {
        type = types.nullOr types.nonEmptyStr;
        default = null;
      };
      address = mkOption {
        type = types.nullOr ipv4;
        default = null;
      };
      description = option types.str;
      enabled = mkOption {
        type = types.bool;
        default = true;
      };
      adguardOrder = mkOption {
        type = types.nullOr types.ints.positive;
        default = null;
      };
      unboundOrder = mkOption {
        type = types.nullOr types.ints.positive;
        default = null;
      };
      coredns = mkOption {
        type = types.nullOr (
          types.submodule {
            options = {
              server = option (
                types.enum [
                  "registry"
                  "internal"
                ]
              );
              order = option types.ints.positive;
            };
          }
        );
        default = null;
      };
    };
  };
  staticRoute = types.submodule {
    options = {
      network = option cidr;
      gatewayName = option (types.strMatching "[a-zA-Z0-9_-]{1,32}");
      gatewayHost = option types.nonEmptyStr;
      gatewayInterface = option types.nonEmptyStr;
      description = option types.str;
      enabled = mkOption {
        type = types.bool;
        default = true;
      };
    };
  };
in
{
  inherit
    ipv4
    network
    host
    dnsRecord
    staticRoute
    ;
}
