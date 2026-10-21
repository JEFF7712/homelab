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
      description = "Runtime path of the MQTT broker password file, outside the store.";
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

    devices = lib.mkOption {
      type = lib.types.attrsOf (
        lib.types.submodule (
          { name, ... }:
          {
            options = {
              enable = lib.mkOption {
                type = lib.types.bool;
                default = true;
              };
              mac = lib.mkOption {
                type = lib.types.str;
                description = "Bluetooth MAC address of the light strip";
              };
              name = lib.mkOption {
                type = lib.types.str;
                default = "Apollo LED Strip";
                description = "Friendly name for Home Assistant discovery";
              };
              objectId = lib.mkOption {
                type = lib.types.str;
                default = "apollo_led_strip";
                description = "Home Assistant object_id";
              };
              area = lib.mkOption {
                type = lib.types.str;
                default = "Living Room";
                description = "Home Assistant suggested area";
              };
              udpPort = lib.mkOption {
                type = lib.types.int;
                default = 21324;
                description = "UDP port for LedFx music mode frames";
              };
            };
          }
        )
      );
      default = { };
      description = "Attribute set of Apollo BLE devices to bridge";
    };
  };

  config = lib.mkIf cfg.enable (
    let
      activeDevices =
        if cfg.devices == { } then
          {
            default = {
              enable = true;
              mac = cfg.mac;
              name = cfg.name;
              objectId = "apollo_led_strip";
              area = "Living Room";
              udpPort = cfg.udpPort;
            };
          }
        else
          lib.filterAttrs (_: dev: dev.enable) cfg.devices;

      makeService = devName: devCfg: {
        description = "Apollo BLE LED Strip MQTT Bridge (${devCfg.name})";
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
            --mac "${devCfg.mac}" \
            --name "${devCfg.name}" \
            --object-id "${devCfg.objectId}" \
            --area "${devCfg.area}" \
            --mqtt-host "${cfg.mqttHost}" \
            --mqtt-port "${toString cfg.mqttPort}" \
            --mqtt-user "${cfg.mqttUser}" \
            --udp-port "${toString devCfg.udpPort}" \
            --state-file "${toString cfg.stateDir}/${devName}.json"
        '';
      };
    in
    {
      environment.persistence."/persist".directories = [
        (toString cfg.stateDir)
      ];

      systemd.services =
        if cfg.devices == { } then
          {
            apollo-ble-bridge = makeService "default" {
              enable = true;
              mac = cfg.mac;
              name = cfg.name;
              objectId = "apollo_led_strip";
              area = "Living Room";
              udpPort = cfg.udpPort;
            };
          }
        else
          lib.mapAttrs' (
            devName: devCfg: lib.nameValuePair "apollo-ble-bridge-${devName}" (makeService devName devCfg)
          ) activeDevices;
    }
  );
}
