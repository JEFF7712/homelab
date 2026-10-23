{ lib }:
let
  inherit (lib) types mkOption;
  option = type: mkOption { inherit type; };
  ipv4 = types.addCheck types.nonEmptyStr (
    value:
    builtins.match "[0-9]{1,3}(\\.[0-9]{1,3}){3}" value != null
    && builtins.all (part: lib.toInt part <= 255) (lib.splitString "." value)
  );
  network = types.submodule {
    options = {
      description = option types.nonEmptyStr;
      parent = option types.nonEmptyStr;
      tag = option (types.ints.between 1 4094);
      interfaceName = option types.nonEmptyStr;
      gateway = option ipv4;
      cidr = option (
        types.addCheck (types.strMatching "[0-9.]+/[0-9]{1,2}") (
          value:
          let
            parts = lib.splitString "/" value;
          in
          ipv4.check (builtins.head parts) && lib.toInt (builtins.elemAt parts 1) <= 32
        )
      );
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
in
{
  inherit ipv4 network host;
}
