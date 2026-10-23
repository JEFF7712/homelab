{
  pkgs,
  tofunix,
  nixosConfigurations,
}:
let
  inherit (pkgs) lib;
  load =
    module:
    import ./. {
      inherit lib;
      extraModules = [ module ];
    };
  inventory = import ./. { inherit lib; };
  rejects = value: !(builtins.tryEval (builtins.deepSeq value true)).success;
  moved = load { hosts.homelab-02.address = lib.mkForce "10.0.30.22"; };
  movedRouter = load { networks.infrastructure.gateway = lib.mkForce "10.0.30.254"; };
  rendered =
    (import ../tofu/opnsense {
      inherit pkgs tofunix;
      fleetInventory = moved;
    }).terraform;
  senderAliases = (builtins.head rendered.resource.opnsense_firewall_alias).managed;
  hosts = lib.filterAttrs (name: host: name == host.config.networking.hostName) nixosConfigurations;
  checks = {
    registryHostMapping = builtins.all (
      name:
      let
        names = hosts.${name}.config.networking.hosts.${inventory.hosts.nas-01.address};
      in
      builtins.elem "registry.rupan.dev" names && builtins.elem "npm.rupan.dev" names
    ) inventory.k3sHosts;
    duplicateDeploymentOrder = rejects (load {
      hosts.homelab-02.deploymentOrder = lib.mkForce 3;
    });
    inconsistentDeploymentType = rejects (load {
      hosts.homelab-02.deploymentType = lib.mkForce "appliance-dns";
    });
    movedDeployerAddress = moved.deployment.homelab-02.ip == "10.0.30.22";
    duplicateAddress = rejects (load {
      hosts.homelab-02.address = lib.mkForce "10.0.30.11";
    });
    invalidAddress = rejects (load {
      hosts.homelab-02.address = lib.mkForce "999.0.30.12";
    });
    invalidCIDR = rejects (load {
      networks.infrastructure.cidr = lib.mkForce "10.0.30.0/99";
    });
    unknownNetwork = rejects (load {
      hosts.homelab-02.network = lib.mkForce "missing";
    });
    addressOutsideNetwork = rejects (load {
      hosts.homelab-02.address = lib.mkForce "10.0.20.12";
    });
    networkAddress = rejects (load {
      hosts.homelab-02.address = lib.mkForce "10.0.30.0";
    });
    gatewayAddress = rejects (load {
      hosts.homelab-02.address = lib.mkForce "10.0.30.1";
    });
    broadcastAddress = rejects (load {
      hosts.homelab-02.address = lib.mkForce "10.0.30.255";
    });
    duplicateVLAN = rejects (load {
      networks.clients.tag = lib.mkForce 30;
    });
    overlappingNetworks = rejects (load {
      networks.clients.cidr = lib.mkForce "10.0.30.0/24";
      networks.clients.gateway = lib.mkForce "10.0.30.2";
    });
    invalidBootstrap = rejects (load {
      bootstrapHost = lib.mkForce "missing";
    });
    nonMemberBootstrap = rejects (load {
      bootstrapHost = lib.mkForce "nas-01";
    });
    movedHostCIDR = moved.hostCIDRs.homelab-02 == "10.0.30.22/24";
    movedHostAlias =
      builtins.elem "10.0.30.22" rendered.locals.firewall_aliases.spotify_matrix_senders.content
      && !(builtins.elem "10.0.30.12" rendered.locals.firewall_aliases.spotify_matrix_senders.content);
    movedRouterDNS = builtins.elem "10.0.30.254" movedRouter.networks.infrastructure.dhcp.dns_servers;
    preservedResourceIdentity = senderAliases.for_each == "\${local.firewall_aliases}";
    canonicalHostSet = builtins.attrNames hosts == builtins.attrNames inventory.hosts;
    hostSettingsMatchInventory = builtins.all (
      name:
      let
        config = hosts.${name}.config;
        declared = inventory.hosts.${name};
      in
      config.systemd.network.networks."30-infrastructure".networkConfig.Address
      == inventory.hostCIDRs.${name}
      &&
        config.systemd.network.networks."30-infrastructure".networkConfig.Gateway
        == inventory.networks.${declared.network}.gateway
      && (config.homelab.k3s.enable or false) == declared.k3s
      && (
        !declared.k3s
        || (
          config.homelab.k3s.nodeIp == declared.address
          && config.homelab.k3s.clusterInit == (name == inventory.bootstrapHost)
          && (name == inventory.bootstrapHost || config.homelab.k3s.serverAddress == inventory.k3sEndpoint)
        )
      )
    ) (builtins.attrNames inventory.hosts);
  };
in
assert lib.assertMsg (builtins.all (value: value) (
  builtins.attrValues checks
)) "Shared inventory regression failures: ${builtins.toJSON checks}";
pkgs.writeText "fleet-inventory-checks.json" (builtins.toJSON checks)
