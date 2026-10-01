{ config, lib, ... }:
{
  options.homelab.base = {
    tmpfsSize = lib.mkOption {
      type = lib.types.str;
      default = "4G";
      description = "Size of the tmpfs root filesystem. adguard-netbird-01 overrides to 2G.";
    };
  };

  config = {
    boot.initrd.systemd.enable = lib.mkDefault true;
    boot.loader.grub.configurationLimit = lib.mkDefault 10;
    boot.loader.systemd-boot.configurationLimit = lib.mkDefault 10;

    hardware.enableRedistributableFirmware = lib.mkDefault true;
    time.timeZone = lib.mkDefault "America/Chicago";

    networking.useNetworkd = lib.mkDefault true;
    networking.nftables.enable = lib.mkDefault true;

    fileSystems."/" = {
      device = "none";
      fsType = "tmpfs";
      options = [
        "defaults"
        "mode=755"
        "size=${config.homelab.base.tmpfsSize}"
      ];
    };
    fileSystems."/persist".neededForBoot = lib.mkDefault true;

    services.openssh = {
      enable = true;
      openFirewall = false;
      hostKeys = [
        {
          path = "/persist/etc/ssh/ssh_host_ed25519_key";
          type = "ed25519";
        }
        {
          bits = 4096;
          path = "/persist/etc/ssh/ssh_host_rsa_key";
          type = "rsa";
        }
      ];
      settings = {
        KbdInteractiveAuthentication = false;
        PasswordAuthentication = false;
        PermitRootLogin = "no";
      };
    };

    users = {
      mutableUsers = false;
      users.rupan = {
        isNormalUser = true;
        extraGroups = [ "wheel" ];
        openssh.authorizedKeys.keys = [
          "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILW7MVmIGzW4Eq1NJm4+gsGwQ+iL44bIfyAa/wdQ1srQ"
          "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFTXsL6q+O1d29vTd3TtK2F0MYPggS5KSHryvlIFBS1K"
          "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFWt07ttKX2X+E5CbwL4To1AuwuwuIaKWUePIrAwGrK0 homelab-nas-deploy"
        ];
      };
    };
    security.sudo.wheelNeedsPassword = false;

    services.smartd = {
      enable = true;
      autodetect = true;
    };

    nix = {
      gc = {
        automatic = true;
        dates = "weekly";
        options = "--delete-older-than 30d";
      };
      settings = {
        auto-optimise-store = true;
        experimental-features = [
          "nix-command"
          "flakes"
        ];
        extra-substituters = [ "http://10.0.30.20:8080/homelab" ];
        extra-trusted-public-keys = [ "homelab:J+OVQOCG2sNT2KoVbWGPikoWcIbBanHnY2NOcMF3vwk=" ];
        trusted-users = [
          "root"
          "@wheel"
        ];
      };
    };

    zramSwap = {
      enable = true;
      memoryPercent = 25;
    };

    system.stateVersion = "26.05";
  };
}
