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

  pairerPython = pkgs.python3.withPackages (ps: [ ps.paho-mqtt ]);

  pairerScript = pkgs.writeTextFile {
    name = "controller-pairer";
    executable = true;
    destination = "/bin/controller-pairer";
    text = builtins.readFile ./controller-pairer.py;
  };
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

    controllerPairer = {
      enable = lib.mkEnableOption "Home Assistant MQTT Bluetooth controller pairer service";

      mqttHost = lib.mkOption {
        type = lib.types.str;
        default = "10.0.30.10";
        description = "MQTT broker host";
      };

      mqttPort = lib.mkOption {
        type = lib.types.int;
        default = 1883;
        description = "MQTT broker port";
      };

      mqttUser = lib.mkOption {
        type = lib.types.str;
        default = "roku-bridge";
        description = "MQTT broker user";
      };

      passwordFile = lib.mkOption {
        type = lib.types.nullOr lib.types.path;
        default = null;
        description = "Path to file containing MQTT password";
      };

      scanTimeout = lib.mkOption {
        type = lib.types.int;
        default = 60;
        description = "Scan timeout in seconds";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [
      pkgs.moonlight-qt
      cfg.sessionPackage
      pkgs.bluez
      pkgs.bluez-tools
      (pkgs.writeShellScriptBin "pair-controller" ''
        exec ${pairerPython}/bin/python3 ${pairerScript}/bin/controller-pairer --cli "$@"
      '')
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

    # Disable Enhanced Retransmission Mode (ERTM) to prevent Bluetooth controller disconnects
    boot.extraModprobeConfig = ''
      options bluetooth disable_ertm=1
    '';

    # Bluetooth stack support
    hardware.bluetooth = {
      enable = lib.mkDefault true;
      powerOnBoot = lib.mkDefault true;
      settings = {
        General = {
          FastConnectable = lib.mkDefault true;
          JustWorksRepairing = lib.mkDefault "always";
        };
      };
      input = {
        General = {
          ClassicBondedOnly = false;
        };
      };
    };

    # Headless Bluetooth agent to authorize Just-Works controller pairing without manual prompts
    systemd.services.bt-agent = {
      description = "Bluetooth NoInputNoOutput pairing agent";
      wantedBy = [ "bluetooth.target" ];
      after = [ "bluetooth.service" ];
      requires = [ "bluetooth.service" ];
      serviceConfig = {
        ExecStart = "${pkgs.bluez-tools}/bin/bt-agent --capability=NoInputNoOutput";
        Restart = "always";
        RestartSec = "5s";
      };
    };

    # Automated Bluetooth game controller pairing daemon connected to Home Assistant MQTT
    systemd.services.controller-pairer = lib.mkIf cfg.controllerPairer.enable {
      description = "Home Assistant Bluetooth Game Controller Pairer";
      wantedBy = [ "multi-user.target" ];
      after = [
        "network-online.target"
        "bluetooth.service"
        "bt-agent.service"
      ];
      wants = [
        "network-online.target"
        "bluetooth.service"
        "bt-agent.service"
      ];
      unitConfig.ConditionPathExists = lib.optional (cfg.controllerPairer.passwordFile != null) (
        toString cfg.controllerPairer.passwordFile
      );
      path = [
        pkgs.bluez
        pkgs.bluez-tools
        pkgs.coreutils
      ];
      serviceConfig = {
        Type = "simple";
        User = "root";
        Restart = "always";
        RestartSec = "5s";
      };
      script = ''
        ${lib.optionalString (cfg.controllerPairer.passwordFile != null) ''
          export MQTT_PASS="$(${pkgs.coreutils}/bin/cat ${cfg.controllerPairer.passwordFile})"
        ''}
        exec ${pairerPython}/bin/python3 -u ${pairerScript}/bin/controller-pairer \
          --mqtt-host "${cfg.controllerPairer.mqttHost}" \
          --mqtt-port "${toString cfg.controllerPairer.mqttPort}" \
          --mqtt-user "${cfg.controllerPairer.mqttUser}" \
          --timeout "${toString cfg.controllerPairer.scanTimeout}"
      '';
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
