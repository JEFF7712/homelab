{
  imports = [
    ./disk-config.nix
    ./hardware-configuration.nix
    ../../modules/common-base.nix
    ../../modules/adguard-netbird-appliance.nix
    ../../modules/switch-tofu.nix
    ../../modules/host-log-shipping.nix
  ];

  networking.hostName = "adguard-netbird-01";

  homelab.base.tmpfsSize = "2G";

  # Journal to Loki, kept small on this 8 GB appliance.
  homelab.logShipping = {
    enable = true;
    goMemLimit = "96MiB";
    memoryHigh = "128M";
    memoryMax = "192M";
  };

  fileSystems."/mnt/backups" = {
    device = "/dev/disk/by-uuid/00d3f54f-28f8-454c-929a-8c8191ab3624";
    fsType = "ext4";
    options = [
      "nofail"
      "x-systemd.device-timeout=10s"
      "noatime"
    ];
  };

  sops.defaultSopsFile = ../../../secrets/hosts/adguard-netbird-01.sops.yaml;
}
