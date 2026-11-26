{ fleetInventory, ... }:
{
  imports = [
    ./hardware-configuration.nix
    ../../modules/disko-single-disk.nix
    ../../modules/k3s-joiner.nix
  ];

  homelab.disk.device = "/dev/disk/by-id/ata-FORESEE_64GB_SSD_0000007798__FMA39721";

  # Local database/index volumes; bulk assets and nightly backups stay on NAS.
  systemd.tmpfiles.rules = [
    "d /persist/audiobookshelf 0750 1000 1000 -"
    "d /persist/bindery 0750 1000 1000 -"
    "d /persist/karakeep 0750 1000 1000 -"
    "d /persist/karakeep-search 0750 1000 1000 -"
    "d /persist/romm-db 0750 999 999 -"
  ];

  homelab.joiner = {
    enable = true;
    hostName = "homelab-03";
    primaryInterface = "enp1s0";
    nodeIp = fleetInventory.hosts.homelab-03.address;
    serverAddress = fleetInventory.k3sEndpoint;
  };
}
