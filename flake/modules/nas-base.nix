{ pkgs, ... }:
{
  boot = {
    loader = {
      efi.canTouchEfiVariables = true;
      grub = {
        enable = true;
        efiSupport = true;
        devices = [ "nodev" ];
        mirroredBoots = [
          {
            path = "/boot";
            devices = [ "/dev/disk/by-id/nvme-WDC_PC_SN520_SDAPNUW-256G-1006_2022C1800396" ];
            efiBootloaderId = "NixOS-a";
          }
          {
            path = "/boot-fallback";
            devices = [ "/dev/disk/by-id/nvme-WDC_PC_SN520_SDAPNUW-256G-1006_2022BA804857" ];
            efiBootloaderId = "NixOS-b";
          }
        ];
      };
    };
    supportedFilesystems = [ "zfs" ];
    zfs.forceImportRoot = false;
    extraModprobeConfig = ''
      options zfs zfs_arc_max=10737418240
    '';
  };

  networking.hostId = "31eabe12";

  networking = {
    useDHCP = false;
    firewall = {
      enable = true;
      extraInputRules = ''
        ip saddr 10.0.10.0/24 tcp dport 22 accept
        ip saddr 10.0.30.0/24 tcp dport 22 accept
        ip saddr 100.64.0.0/10 tcp dport 22 accept
        ip saddr 10.0.10.0/24 tcp dport 9633 accept
        ip saddr 10.0.30.0/24 tcp dport 9633 accept
        ip saddr 10.42.0.0/16 tcp dport 9633 accept
      '';
    };
  };

  # PipeWire runs here on its compiled-in defaults, which do not include the
  # Pulse protocol shim. The pulse socket unit therefore accepts nothing and
  # every Pulse client is refused. The Wyoming satellite uses pw-play (native
  # PipeWire) so it never noticed, but the bedroom LedFx analyses audio through
  # a Pulse source and needs the shim to read the Bluetooth monitor.
  environment.etc."pipewire/pipewire.conf".text = ''
    context.modules = {
      libpipewire-module-protocol-pulse = { }
    }
  '';

  services.prometheus.exporters.smartctl = {
    enable = true;
    port = 9633;
    listenAddress = "0.0.0.0";
    maxInterval = "60s";
  };

  services.udev.extraRules = ''
    SUBSYSTEM=="nvme", KERNEL=="nvme[0-9]*", GROUP="disk", MODE="0660"
  '';

  systemd.network = {
    enable = true;
    networks."30-infrastructure" = {
      matchConfig.Name = "enp5s0";
      networkConfig = {
        Address = "10.0.30.20/24";
        DNS = "10.0.30.10";
        Gateway = "10.0.30.1";
      };
      linkConfig.RequiredForOnline = "routable";
    };
  };

  services.zfs.autoScrub = {
    enable = true;
    interval = "weekly";
  };
  services.zfs.trim = {
    enable = true;
    interval = "weekly";
  };

  systemd.tmpfiles.rules = [
    "d /persist/etc/ssh 0700 root root -"
    "d /persist/var/lib/smartmontools 0755 root root -"
  ];

  environment.persistence."/persist" = {
    hideMounts = true;
    directories = [
      "/var/lib/nixos"
      "/var/lib/smartmontools"
      "/var/lib/systemd"
    ];
    files = [ "/etc/machine-id" ];
  };

  environment.systemPackages = with pkgs; [
    attic-client
    cryptsetup
    curl
    dmidecode
    ethtool
    lm_sensors
    nvme-cli
    pciutils
    smartmontools
    tcpdump
    zfs
  ];
}
