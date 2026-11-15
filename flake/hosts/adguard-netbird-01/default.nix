{
  imports = [
    ./disk-config.nix
    ./hardware-configuration.nix
    ../../modules/common-base.nix
    ../../modules/adguard-netbird-appliance.nix
    ../../modules/switch-tofu.nix
  ];

  networking.hostName = "adguard-netbird-01";

  homelab.base.tmpfsSize = "2G";

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
