{
  lib,
  extraModules ? [ ],
}:
let
  inherit (lib) mkOption types;
  sharedTypes = import ./types.nix { inherit lib; };
  evaluated = lib.evalModules {
    modules = [
      {
        options = {
          hosts = mkOption { type = types.attrsOf sharedTypes.host; };
          networks = mkOption { type = types.attrsOf sharedTypes.network; };
          bootstrapHost = mkOption { type = types.nonEmptyStr; };
        };
      }
      ./hosts.nix
      ./networks.nix
    ]
    ++ extraModules;
  };
  cfg = evaluated.config;
  unique = values: builtins.length values == builtins.length (lib.unique values);
  ipNumber =
    address: lib.foldl' (value: octet: value * 256 + lib.toInt octet) 0 (lib.splitString "." address);
  power = n: if n == 0 then 1 else 2 * power (n - 1);
  ranges = lib.mapAttrs (
    _: network:
    let
      parts = lib.splitString "/" network.cidr;
      prefix = lib.toInt (builtins.elemAt parts 1);
      start = ipNumber (builtins.head parts);
      size = if prefix >= 0 && prefix <= 32 then power (32 - prefix) else 0;
    in
    {
      inherit prefix start size;
      end = start + size - 1;
    }
  ) cfg.networks;
  usable = address: range: ipNumber address > range.start && ipNumber address < range.end;
  assertions = [
    {
      assertion = unique (map (host: host.deploymentOrder) (builtins.attrValues cfg.hosts));
      message = "Fleet deployment order must be unique.";
    }
    {
      assertion = builtins.all (
        host:
        host.k3s == builtins.elem host.deploymentType [
          "k3s-worker"
          "k3s-stateful"
        ]
      ) (builtins.attrValues cfg.hosts);
      message = "Fleet deployment types must agree with k3s membership.";
    }
    {
      assertion = unique (
        map (network: "${network.parent}:${toString network.tag}") (builtins.attrValues cfg.networks)
      );
      message = "VLAN tags must be unique on each parent interface.";
    }
    {
      assertion = unique (map (network: network.interfaceName) (builtins.attrValues cfg.networks));
      message = "Interface names must be unique.";
    }
    {
      assertion = builtins.all (
        name:
        builtins.stringLength name <= 63 && builtins.match "[a-z0-9]([-a-z0-9]*[a-z0-9])?" name != null
      ) (builtins.attrNames cfg.hosts);
      message = "Fleet host keys must be valid DNS hostnames.";
    }
    {
      assertion = unique (map (host: host.address) (builtins.attrValues cfg.hosts));
      message = "Fleet host addresses must be unique.";
    }
    {
      assertion = builtins.all (host: builtins.hasAttr host.network cfg.networks) (
        builtins.attrValues cfg.hosts
      );
      message = "Fleet hosts must reference declared networks.";
    }
    {
      assertion = builtins.all (
        name:
        let
          r = ranges.${name};
        in
        r.size > 0
        && builtins.div r.start r.size * r.size == r.start
        && usable cfg.networks.${name}.gateway r
      ) (builtins.attrNames ranges);
      message = "Networks must have aligned IPv4 CIDRs and usable gateways.";
    }
    {
      assertion = builtins.all (
        a:
        builtins.all (
          b: a == b || ranges.${a}.end < ranges.${b}.start || ranges.${b}.end < ranges.${a}.start
        ) (builtins.attrNames ranges)
      ) (builtins.attrNames ranges);
      message = "Fleet networks must not overlap.";
    }
    {
      assertion = builtins.all (
        host:
        builtins.hasAttr host.network ranges
        && usable host.address ranges.${host.network}
        && host.address != cfg.networks.${host.network}.gateway
      ) (builtins.attrValues cfg.hosts);
      message = "Fleet host addresses must be usable in their declared network.";
    }
    {
      assertion = builtins.hasAttr cfg.bootstrapHost cfg.hosts && cfg.hosts.${cfg.bootstrapHost}.k3s;
      message = "The k3s bootstrap host must be a declared k3s member.";
    }
  ];
in
lib.asserts.checkAssertWarn assertions [ ] {
  inherit (cfg) networks hosts bootstrapHost;
  hostCIDRs = lib.mapAttrs (
    _: host: "${host.address}/${toString ranges.${host.network}.prefix}"
  ) cfg.hosts;
  k3sHosts = builtins.attrNames (lib.filterAttrs (_: host: host.k3s) cfg.hosts);
  k3sEndpoint = "https://${cfg.hosts.${cfg.bootstrapHost}.address}:6443";
  deployment = lib.mapAttrs (name: host: {
    inherit name;
    ip = host.address;
    host_type = host.deploymentType;
    order = host.deploymentOrder;
  }) cfg.hosts;
}
