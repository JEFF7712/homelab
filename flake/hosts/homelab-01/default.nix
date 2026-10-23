{ fleetInventory, ... }:
{
  imports = [
    ./disk-config.nix
    ./hardware-configuration.nix
    ../../modules/common-base.nix
    ../../modules/k3s-server.nix
  ];

  networking.hostName = "homelab-01";

  homelab.k3s = {
    enable = true;
    primaryInterface = "eno1";
    nodeIp = fleetInventory.hosts.homelab-01.address;
    clusterInit = true;
    bootstrapCilium = true;
  };

  services.k3s.extraFlags = [
    "--kubelet-arg=system-reserved=cpu=1,memory=2Gi"
    "--kubelet-arg=kube-reserved=cpu=500m,memory=1Gi"
  ];
}
