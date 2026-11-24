# Gaming desktop at another house, reached over NetBird. Not an inventory host:
# it has no LAN address, DHCP lease or firewall alias. Deployed by the
# deploy-home-pc-01 operation (docs/runbooks/home-pc-01.md).
{ pkgs, ... }:
{
  imports = [
    ./hardware-configuration.nix
    ./access.nix
    ./offsite-backup.nix
  ];

  boot.loader.systemd-boot = {
    enable = true;
    configurationLimit = 10;
  };
  boot.loader.efi.canTouchEfiVariables = true;
  # Newer amdgpu; the installer kernel hung in amdgpu flip_done timeouts.
  boot.kernelPackages = pkgs.linuxPackages_latest;
  hardware.enableRedistributableFirmware = true;
  # First-gen Ryzen (1700) can freeze solid in the deep C6 idle state, which
  # acpi_idle exposes as C2 here; a boot froze ~15s in with no log. Keep C1.
  boot.kernelParams = [ "processor.max_cstate=1" ];
  # Remote host: reboot on lockups instead of hanging.
  boot.kernel.sysctl = {
    "kernel.softlockup_panic" = 1;
    "kernel.hardlockup_panic" = 1;
    "kernel.panic" = 10;
  };
  # A suspended remote host drops off NetBird: Plasma's idle suspend took it
  # offline for three hours on 2026-11-23. Screens still blank; the machine
  # never sleeps.
  systemd.targets = {
    sleep.enable = false;
    suspend.enable = false;
    hibernate.enable = false;
    hybrid-sleep.enable = false;
    suspend-then-hibernate.enable = false;
  };
  # SP5100 TCO hardware watchdog resets the machine if systemd stops petting it.
  systemd.settings.Manager = {
    RuntimeWatchdogSec = "30s";
    RebootWatchdogSec = "5min";
  };
  hardware.graphics = {
    enable = true;
    enable32Bit = true;
  };
  # The HP firmware's ACPI TPM2 table is broken (tpm_crb_acpi probe fails with
  # -EBUSY), so /dev/tpm0 never appears and systemd waits 90s for it twice.
  systemd.tpm2.enable = false;
  boot.initrd.systemd.tpm2.enable = false;

  networking.hostName = "home-pc-01";
  networking.networkmanager.enable = true;
  time.timeZone = "America/Chicago";
  i18n.defaultLocale = "en_US.UTF-8";

  services.displayManager.sddm = {
    enable = true;
    wayland.enable = true;
  };
  services.desktopManager.plasma6.enable = true;
  services.xserver.xkb.layout = "us";

  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
  };

  nixpkgs.config.allowUnfree = true;
  programs.steam = {
    enable = true;
    extraCompatPackages = [ pkgs.proton-ge-bin ];
  };
  programs.gamemode.enable = true;
  programs.firefox.enable = true;
  environment.systemPackages = with pkgs; [
    flightgear
    git
    htop
    # jstest/jscal for checking the Honeycomb Alpha and Bravo.
    linuxConsoleTools
    mangohud
    nano
    prismlauncher
    rsync
    vim
    wget
  ];

  # FlightGear binds the Honeycomb Alpha and Bravo through its HID input
  # (Input/HID/HoneycombAeronautical), which opens hidraw: the Bravo's 48
  # buttons exceed the joystick API, and its autopilot and gear LEDs are HID
  # output reports. Give the seated user access; uaccess must be tagged before
  # 73-seat-late.rules, so this cannot live in extraRules (99-local).
  services.udev.packages = [
    (pkgs.writeTextFile {
      name = "honeycomb-hidraw-udev-rules";
      destination = "/etc/udev/rules.d/70-honeycomb-hidraw.rules";
      text = ''
        KERNEL=="hidraw*", ATTRS{idVendor}=="294b", TAG+="uaccess"
      '';
    })
  ];

  # The host is managed from the homelab repo. A leftover installer config
  # would let a plain `nixos-rebuild` undo it; bootstrap files are spent.
  systemd.tmpfiles.rules = [
    "r /etc/nixos/configuration.nix"
    "r /etc/nixos/hardware-configuration.nix"
    "R /root/homelab-src"
    "r /root/bootstrap*"
    "r /root/probe*"
    "r /root/.ssh/tunnel"
    "r /root/.ssh/tunnel.pub"
  ];

  services.netbird.clients.default = {
    name = "netbird";
    port = 51820;
    interface = "wt0";
    autoStart = true;
  };

  # Family members set their own passwords with passwd.
  users.mutableUsers = true;
  users.users = {
    rupan = {
      isNormalUser = true;
      description = "Rupan";
      extraGroups = [
        "wheel"
        "networkmanager"
      ];
      initialPassword = "changeme";
    };
    mala = {
      isNormalUser = true;
      description = "Mala";
      extraGroups = [ "networkmanager" ];
      initialPassword = "changeme";
    };
    pandyan = {
      isNormalUser = true;
      description = "Pandyan";
      extraGroups = [ "networkmanager" ];
      initialPassword = "changeme";
    };
  };

  # The former Windows space (sda1). nofail keeps the host booting and reachable
  # if this disk ever fails to mount; /home then falls back to the root fs.
  fileSystems."/home" = {
    device = "/dev/disk/by-uuid/1b173afa-9b7f-4627-9ec2-711d448cd1e1";
    fsType = "ext4";
    options = [
      "nofail"
      "x-systemd.device-timeout=15s"
    ];
  };

  swapDevices = [
    {
      device = "/swapfile";
      size = 8192;
    }
  ];

  nix.settings.experimental-features = [
    "nix-command"
    "flakes"
  ];
  nix.gc = {
    automatic = true;
    dates = "weekly";
    options = "--delete-older-than 14d";
  };

  system.stateVersion = "26.05";
}
