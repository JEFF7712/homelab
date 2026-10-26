{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.moonlight;

  moonlightSession = pkgs.writeShellScriptBin "moonlight-session" ''
    set -euo pipefail

    # Switch to gaming workspace in Sway if running under Sway
    if [ -n "''${SWAYSOCK:-}" ] || ${pkgs.sway}/bin/swaymsg -t get_version >/dev/null 2>&1; then
      ${pkgs.sway}/bin/swaymsg workspace 2 || true
    fi

    # Launch Moonlight with any passed arguments
    ${pkgs.moonlight-qt}/bin/moonlight-qt "$@" || true

    # Switch back to kiosk workspace in Sway when Moonlight exits
    if [ -n "''${SWAYSOCK:-}" ] || ${pkgs.sway}/bin/swaymsg -t get_version >/dev/null 2>&1; then
      ${pkgs.sway}/bin/swaymsg workspace 1 || true
    fi
  '';
in
{
  options.homelab.moonlight = {
    enable = lib.mkEnableOption "Moonlight game streaming client";

    user = lib.mkOption {
      type = lib.types.str;
      default = "kiosk";
      description = "User account under which Moonlight runs";
    };

    autostart = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Whether to automatically launch Moonlight on boot in the session";
    };

    output = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Display output connector dedicated to Moonlight (e.g. DP-1 or DP-2)";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [
      pkgs.moonlight-qt
      moonlightSession
    ];

    # Controller and input support
    hardware.uinput.enable = true;
    hardware.steam-hardware.enable = true;

    # Nintendo Switch Pro & Joy-Con pairing and mapping daemon
    services.joycond.enable = true;

    # Kernel modules for Nintendo, PlayStation, and uinput controllers
    boot.kernelModules = [
      "hid-nintendo"
      "hid-playstation"
      "hid-sony"
      "uinput"
    ];

    # Bluetooth support for wireless controller connections
    hardware.bluetooth = {
      enable = lib.mkDefault true;
      powerOnBoot = lib.mkDefault true;
    };

    # Ensure user has access to display, rendering, audio, and controller input devices
    users.users.${cfg.user}.extraGroups = [
      "video"
      "render"
      "input"
      "audio"
    ];
    users.users.rupan.extraGroups = [
      "video"
      "render"
      "input"
      "audio"
    ];

    # Ephemeral root (tmpfs): persist Moonlight pairing tokens, PIN certificates, and settings
    environment.persistence."/persist" = {
      directories = lib.mkDefault [
        "/var/lib/bluetooth"
      ];
      users = {
        ${cfg.user}.directories = [
          ".config/Moonlight Game Streaming Project"
          ".cache/Moonlight Game Streaming Project"
        ];
        rupan.directories = [
          ".config/Moonlight Game Streaming Project"
          ".cache/Moonlight Game Streaming Project"
        ];
      };
    };
  };
}
