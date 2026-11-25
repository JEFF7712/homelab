# Family desktop experience: a quiet, branded boot, matching login screen,
# complete fonts, everyday apps and the usual desktop services.
{ pkgs, ... }:
{
  # Boot splash with the firmware (HP) logo instead of scrolling text. amdgpu
  # loads in the (systemd) initrd so the splash hands over to SDDM without a
  # mode switch; status and udev chatter stay off the console.
  boot.plymouth = {
    enable = true;
    theme = "bgrt";
  };
  boot.initrd.kernelModules = [ "amdgpu" ];
  boot.consoleLogLevel = 3;
  boot.kernelParams = [
    "quiet"
    "splash"
    "rd.systemd.show_status=auto"
    "rd.udev.log_level=3"
    "udev.log_level=3"
  ];
  # No menu on a normal boot; hold Space at power-on to pick an older
  # generation (docs/runbooks/home-pc-01.md).
  boot.loader.timeout = 0;

  fonts = {
    enableDefaultPackages = true;
    packages = with pkgs; [
      noto-fonts
      noto-fonts-cjk-sans
      noto-fonts-color-emoji
      # Metric-compatible with Arial/Times/Courier for Office documents.
      liberation_ttf
    ];
  };

  environment.systemPackages = with pkgs; [
    discord
    google-chrome
    kdePackages.ark
    kdePackages.kcalc
    kdePackages.partitionmanager
    libreoffice-qt6-fresh
    spotify
    vlc
    zoom-us
  ];

  # Network printers are discovered over mDNS and printed to via IPP.
  services.printing.enable = true;
  services.avahi = {
    enable = true;
    nssmdns4 = true;
    openFirewall = true;
  };

  hardware.bluetooth = {
    enable = true;
    powerOnBoot = true;
  };
  # Firmware updates appear in Discover.
  services.fwupd.enable = true;
  programs.kdeconnect.enable = true;

  # Discover installs further apps from Flathub for any user.
  services.flatpak.enable = true;
  systemd.services.flathub-remote = {
    description = "Add the Flathub remote for Discover";
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" ];
    after = [ "network-online.target" ];
    path = [ pkgs.flatpak ];
    serviceConfig = {
      Type = "oneshot";
      Restart = "on-failure";
      RestartSec = "5min";
    };
    script = ''
      flatpak remote-add --system --if-not-exists flathub \
        https://dl.flathub.org/repo/flathub.flatpakrepo
    '';
  };
}
