{
  self,
  nixpkgs,
  nix-topology,
  system,
}:
let
  pkgs = import nixpkgs {
    inherit system;
    overlays = [ nix-topology.overlays.default ];
  };
  inherit (pkgs) lib;
  assignments = builtins.fromJSON (builtins.readFile ../../opnsense_reconciler/assignments.json);
  hosts = lib.filterAttrs (
    name: host: name == host.config.networking.hostName
  ) self.nixosConfigurations;
  diagramNodes = lib.mapAttrs (
    _: host:
    let
      config = host.config;
    in
    {
      deviceType = "nixos";
      interfaces = lib.foldl' lib.recursiveUpdate { } (
        lib.mapAttrsToList (
          _: network:
          let
            names = lib.toList (network.matchConfig.Name or [ ]);
            addresses =
              network.address
              ++ map (address: address.Address) network.addresses
              ++ lib.toList (network.networkConfig.Address or [ ]);
          in
          lib.genAttrs (builtins.filter (name: !(lib.hasInfix "*" name)) names) (name: {
            addresses = map (address: builtins.head (lib.splitString "/" address)) addresses;
            gateways = network.gateway ++ lib.toList (network.networkConfig.Gateway or [ ]);
            virtual = builtins.any (netdev: (netdev.netdevConfig.Name or null) == name) (
              builtins.attrValues config.systemd.network.netdevs
            );
          })
        ) config.systemd.network.networks
      );
      services =
        lib.optionalAttrs config.services.k3s.enable {
          k3s.name = "k3s (${config.services.k3s.role})";
        }
        // lib.optionalAttrs (config.services.homelab-zot-registry.enable or false) {
          zot.name = "Zot registry";
        }
        // lib.optionalAttrs (config.homelab.woodpecker.enable or false) {
          woodpecker.name = "Woodpecker CI";
        }
        // lib.optionalAttrs (config.services.netbird.clients != { }) {
          netbird.name = "NetBird client";
        }
        // lib.optionalAttrs config.services.adguardhome.enable {
          adguardhome.name = "AdGuard Home";
        }
        // lib.optionalAttrs config.services.forgejo.enable {
          forgejo.name = "Forgejo";
        }
        // lib.optionalAttrs config.services.nginx.enable {
          nginx.name = "NGINX";
        }
        // lib.optionalAttrs config.services.openssh.enable {
          openssh.name = "OpenSSH";
        }
        // lib.optionalAttrs config.services.mosquitto.enable {
          mosquitto.name = "Mosquitto";
        }
        // lib.optionalAttrs config.services.zigbee2mqtt.enable {
          zigbee2mqtt.name = "Zigbee2MQTT";
        };
    }
  ) hosts;
  topology = import nix-topology {
    inherit pkgs;
    modules = [
      (
        { ... }:
        {
          networks = builtins.listToAttrs (
            map (assignment: {
              name = assignment.descr;
              value.name = "${assignment.descr} (VLAN ${toString assignment.tag})";
            }) assignments
          );
          nodes = {
            opnsense = {
              name = "OPNsense (external, declared VLANs only)";
              deviceType = "router";
              interfaces = builtins.listToAttrs (
                map (assignment: {
                  name = "vlan${toString assignment.tag}";
                  value = {
                    virtual = true;
                    network = assignment.descr;
                    addresses = [ (builtins.head (lib.splitString "/" assignment.ipaddr)) ];
                  };
                }) assignments
              );
            };
            switch-fabric = {
              name = "Switch fabric (external, port links unknown)";
              deviceType = "switch";
            };
            cloudflare = {
              name = "Cloudflare (external, tunnel paths unmodeled)";
              deviceType = "internet";
            };
          }
          // lib.mapAttrs (
            _: node:
            node
            // {
              interfaces = lib.mapAttrs (
                _: interface:
                let
                  matches = builtins.filter (
                    assignment: builtins.elem (builtins.head (lib.splitString "/" assignment.ipaddr)) interface.gateways
                  ) assignments;
                in
                interface
                // lib.optionalAttrs (builtins.length matches == 1) {
                  network = (builtins.head matches).descr;
                }
              ) node.interfaces;
            }
          ) diagramNodes;
          assertions = [
            {
              assertion = builtins.length (builtins.attrNames hosts) == 7;
              message = "Topology must include the seven canonical fleet hosts, without deployment variants.";
            }
          ];
        }
      )
    ];
  };
  manifest = {
    schemaVersion = 1;
    evidence = "declared configuration, not observed runtime state";
    sourceRevision = self.rev or null;
    sourceNarHash = self.narHash or null;
    inherit assignments;
    bgpPeers = builtins.fromJSON (builtins.readFile ../../opnsense_reconciler/bgp-neighbors.json);
    nodes = lib.mapAttrs (_: node: {
      inherit (node) name deviceType;
      services = lib.mapAttrs (_: service: { inherit (service) name; }) node.services;
      interfaces = lib.mapAttrs (_: interface: {
        inherit (interface)
          addresses
          gateways
          network
          virtual
          physicalConnections
          ;
      }) node.interfaces;
    }) topology.config.nodes;
    gaps = [
      "Physical switch ports, cabling and WAN handoff are not modeled. No physical links are inferred."
      "Kubernetes workloads, service routes, Cloudflare tunnels and NetBird overlay paths are not extracted."
      "BGP peer intent is included in topology.json, not drawn as physical connectivity or verified live."
      "Services are selected explicitly from evaluated configuration, not a complete service inventory."
      "Virtual interfaces without a declared gateway may have no network assignment."
    ];
  };
in
pkgs.runCommand "homelab-topology"
  {
    nativeBuildInputs = [ pkgs.python3 ];
    manifest = builtins.toJSON manifest;
    passAsFile = [ "manifest" ];
  }
  ''
    mkdir -p "$out"
    cp ${topology.config.output}/*.svg "$out/"
    cp "$manifestPath" "$out/topology.json"
    cp ${./README.md} "$out/README.md"
    python - "$out" <<'PY'
    import json
    import sys
    import xml.etree.ElementTree as ET
    from pathlib import Path

    output = Path(sys.argv[1])
    manifest = json.loads((output / "topology.json").read_text())
    for name in ("main.svg", "network.svg"):
        assert ET.parse(output / name).getroot().tag == "{http://www.w3.org/2000/svg}svg"
    assert len([n for n in manifest["nodes"].values() if n["deviceType"] == "nixos"]) == 7
    assert manifest["nodes"]["nas-01"]["services"]["zot"]["name"] == "Zot registry"
    assert manifest["nodes"]["homelab-04"]["services"]["woodpecker"]["name"] == "Woodpecker CI"
    for name, node in manifest["nodes"].items():
        if node["deviceType"] == "nixos":
            assert any(i["network"] == "infrastructure" for i in node["interfaces"].values()), name
    PY
  ''
