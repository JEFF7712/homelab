{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.apollo-ble-bridge;
  bridgePython = pkgs.python3.withPackages (ps: [
    ps.bleak
    ps.paho-mqtt
  ]);
  bridgeDaemon = pkgs.writeTextFile {
    name = "apollo-ble-bridge";
    executable = true;
    destination = "/bin/apollo-ble-bridge";
    text = builtins.readFile ./apollo-ble-bridge.py;
  };
in
{
  options.homelab.apollo-ble-bridge = {
    enable = lib.mkEnableOption "Apollo BLE to Home Assistant MQTT bridge";

    mac = lib.mkOption {
      type = lib.types.str;
      default = "01:05:46:00:3A:71";
      description = "Bluetooth MAC address of the light strip";
    };

    name = lib.mkOption {
      type = lib.types.str;
      default = "Apollo LED Strip";
      description = "Friendly name for Home Assistant discovery";
    };

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
      type = lib.types.path;
      default = "/persist/secrets/mosquitto-apollo-bridge-password";
      description = "Path to MQTT broker password file";
    };

    udpPort = lib.mkOption {
      type = lib.types.int;
      default = 21324;
      description = "UDP port for LedFx music mode frames";
    };

    stateDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/apollo-ble-bridge";
      description = "Directory for bridge persistent state";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.persistence."/persist".directories = [
      (toString cfg.stateDir)
    ];

    systemd.services.apollo-ble-bridge = {
      description = "Apollo BLE LED Strip MQTT Bridge";
      after = [
        "network-online.target"
        "bluetooth.service"
      ];
      wants = [
        "network-online.target"
        "bluetooth.service"
      ];
      wantedBy = [ "multi-user.target" ];

      unitConfig.ConditionPathExists = [
        (toString cfg.passwordFile)
      ];

      serviceConfig = {
        Type = "simple";
        User = "root";
        StateDirectory = "apollo-ble-bridge";
        Restart = "always";
        RestartSec = "5s";
      };

      script = ''
        export MQTT_PASS="$(${pkgs.coreutils}/bin/cat ${cfg.passwordFile})"
        exec ${bridgePython}/bin/python3 -u ${bridgeDaemon}/bin/apollo-ble-bridge \
          --mac "${cfg.mac}" \
          --name "${cfg.name}" \
          --mqtt-host "${cfg.mqttHost}" \
          --mqtt-port "${toString cfg.mqttPort}" \
          --mqtt-user "${cfg.mqttUser}" \
          --udp-port "${toString cfg.udpPort}" \
          --state-file "${toString cfg.stateDir}/state.json"
      '';
    };
  };
}
