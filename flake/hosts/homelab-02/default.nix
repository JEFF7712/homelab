{ fleetInventory, ... }:
{
  imports = [
    ./hardware-configuration.nix
    ../../modules/disko-single-disk.nix
    ../../modules/k3s-joiner.nix
  ];

  homelab.disk.device = "/dev/disk/by-id/ata-FORESEE_64GB_SSD_0000007520__FMA39721";

  homelab.joiner = {
    enable = true;
    hostName = "homelab-02";
    primaryInterface = "enp1s0";
    nodeIp = fleetInventory.hosts.homelab-02.address;
    serverAddress = fleetInventory.k3sEndpoint;
  };
}
