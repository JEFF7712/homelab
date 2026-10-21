{ config, lib, ... }:
let
  users = [
    "home-assistant"
    "zigbee2mqtt"
    "roku-bridge"
    "bus-display"
  ];
  passwordFile = user: config.sops.secrets."mosquitto-${user}-password".path;
in
{
  sops.secrets =
    lib.genAttrs (map (user: "mosquitto-${user}-password") users) (_: {
      restartUnits = [ "mosquitto.service" ];
    })
    // {
      # homelab-05's Apollo bridge shares this broker user.
      mosquitto-roku-bridge-password = {
        sopsFile = ../../../secrets/shared/mqtt-roku-bridge.sops.yaml;
        restartUnits = [ "mosquitto.service" ];
      };
    };

  services.mosquitto = {
    enable = true;
    listeners = [
      {
        address = "10.0.30.10";
        port = 1883;
        users = {
          "home-assistant" = {
            passwordFile = passwordFile "home-assistant";
            acl = [
              "readwrite homeassistant/#"
              "readwrite zigbee2mqtt/#"
              "readwrite roku/#"
              "readwrite bus-display/#"
            ];
          };
          zigbee2mqtt = {
            passwordFile = passwordFile "zigbee2mqtt";
            acl = [
              "readwrite homeassistant/#"
              "readwrite zigbee2mqtt/#"
            ];
          };
          roku-bridge = {
            passwordFile = passwordFile "roku-bridge";
            acl = [
              "readwrite homeassistant/#"
              "readwrite roku/#"
            ];
          };
          bus-display = {
            passwordFile = passwordFile "bus-display";
            acl = [
              "readwrite bus-display/#"
              "write homeassistant/light/bus_display/config"
            ];
          };
        };
      }
    ];
  };
}
