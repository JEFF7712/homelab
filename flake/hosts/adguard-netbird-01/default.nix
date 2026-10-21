{
  imports = [
    ./disk-config.nix
    ./hardware-configuration.nix
    ../../modules/common-base.nix
    ../../modules/adguard-netbird-appliance.nix
  ];

  networking.hostName = "adguard-netbird-01";

  homelab.base.tmpfsSize = "2G";

  sops.defaultSopsFile = ../../../secrets/hosts/adguard-netbird-01.sops.yaml;
}
