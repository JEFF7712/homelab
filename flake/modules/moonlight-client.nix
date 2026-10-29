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

    restore_workspace() {
      if [ -n "''${SWAYSOCK:-}" ] || ${pkgs.sway}/bin/swaymsg -t get_version >/dev/null 2>&1; then
        ${pkgs.sway}/bin/swaymsg workspace 1 || true
      fi
    }
    trap restore_workspace EXIT

    # Switch to gaming workspace in Sway if running under Sway
    if [ -n "''${SWAYSOCK:-}" ] || ${pkgs.sway}/bin/swaymsg -t get_version >/dev/null 2>&1; then
      ${pkgs.sway}/bin/swaymsg workspace 2 || true
    fi

    # Launch Moonlight
    exec ${pkgs.moonlight-qt}/bin/moonlight "$@"
  '';

  autostartScript =
    if cfg.autostartHost != null && cfg.autostartApp != null then
      pkgs.writeShellScript "moonlight-autostart" ''
        # Automatically stream the configured app on startup.
        # Fall back to the Moonlight GUI if streaming finishes or fails.
        ${pkgs.moonlight-qt}/bin/moonlight stream ${lib.escapeShellArg cfg.autostartHost} ${lib.escapeShellArg cfg.autostartApp} || true
        exec ${pkgs.moonlight-qt}/bin/moonlight
      ''
    else
      "${pkgs.moonlight-qt}/bin/moonlight";
in
{
  options.homelab.moonlight = {
    enable = lib.mkEnableOption "Moonlight game streaming client";

    user = lib.mkOption {
      type = lib.types.str;
      default = "kiosk";
      description = "Default user account under which Moonlight runs";
    };

    users = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ cfg.user ];
      description = "User accounts permitted to run Moonlight";
    };

    autostart = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Whether to automatically launch Moonlight on boot in the session";
    };

    autostartHost = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Host IP or hostname to automatically stream on boot";
    };

    autostartApp = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "App name to automatically stream on boot (e.g. Steam Big Picture or Desktop)";
    };

    output = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Display output connector dedicated to Moonlight (e.g. DP-1 or DP-2)";
    };

    sessionPackage = lib.mkOption {
      type = lib.types.package;
      default = moonlightSession;
      readOnly = true;
      description = "Package providing bin/moonlight-session. Reference it by store path from compositor bindings, whose systemd PATH excludes the system profile.";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [
      pkgs.moonlight-qt
      cfg.sessionPackage
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
      "hidp"
      "uinput"
    ];

    # Bluetooth stack support
    hardware.bluetooth = {
      enable = lib.mkDefault true;
      powerOnBoot = lib.mkDefault true;
      input = {
        General = {
          ClassicBondedOnly = false;
        };
      };
    };

    # Ensure users have access to display, rendering, audio, and controller input devices
    users.users = lib.genAttrs cfg.users (_: {
      extraGroups = [
        "video"
        "render"
        "input"
        "audio"
      ];
    });

    # Ephemeral root (tmpfs): persist Moonlight pairing tokens, PIN certificates, and settings
    environment.persistence."/persist".users = lib.genAttrs cfg.users (_: {
      directories = [
        ".config/Moonlight Game Streaming Project"
        ".cache/Moonlight Game Streaming Project"
      ];
    });

    # Wire output and autostart options directly into Sway if kiosk compositor is active
    homelab.kiosk.swayExtraConfig = lib.concatStringsSep "\n" (
      lib.filter (s: s != "") [
        (lib.optionalString (cfg.output != null) ''
          workspace 2 output ${cfg.output}
        '')
        ''
          assign [app_id="(?i)moonlight.*"] workspace 2
          assign [class="(?i)moonlight.*"] workspace 2
        ''
        (lib.optionalString cfg.autostart ''
          exec ${autostartScript}
        '')
      ]
    );
  };
}
