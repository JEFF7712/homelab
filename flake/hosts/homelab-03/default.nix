{ fleetInventory, ... }:
{
  imports = [
    ./hardware-configuration.nix
    ../../modules/disko-single-disk.nix
    ../../modules/k3s-joiner.nix
  ];

  homelab.disk.device = "/dev/disk/by-id/ata-FORESEE_64GB_SSD_0000007798__FMA39721";

  homelab.joiner = {
    enable = true;
    hostName = "homelab-03";
    primaryInterface = "enp1s0";
    nodeIp = fleetInventory.hosts.homelab-03.address;
    serverAddress = fleetInventory.k3sEndpoint;
  };
}
