{ ... }:
{
  services.mosquitto = {
    enable = true;
    listeners = [
      {
        address = "10.0.30.10";
        port = 1883;
        users = {
          "home-assistant" = {
            passwordFile = "/persist/secrets/mosquitto-home-assistant-password";
            acl = [
              "readwrite homeassistant/#"
              "readwrite zigbee2mqtt/#"
              "readwrite roku/#"
              "readwrite bus-display/#"
            ];
          };
          zigbee2mqtt = {
            passwordFile = "/persist/secrets/mosquitto-zigbee2mqtt-password";
            acl = [
              "readwrite homeassistant/#"
              "readwrite zigbee2mqtt/#"
            ];
          };
          roku-bridge = {
            passwordFile = "/persist/secrets/mosquitto-roku-bridge-password";
            acl = [
              "readwrite homeassistant/#"
              "readwrite roku/#"
            ];
          };
          bus-display = {
            passwordFile = "/persist/secrets/mosquitto-bus-display-password";
            acl = [
              "readwrite bus-display/#"
              "write homeassistant/light/bus_display/config"
            ];
          };
        };
      }
    ];
  };

  systemd.services.mosquitto.unitConfig.ConditionPathExists = [
    "/persist/secrets/mosquitto-home-assistant-password"
    "/persist/secrets/mosquitto-zigbee2mqtt-password"
    "/persist/secrets/mosquitto-roku-bridge-password"
    "/persist/secrets/mosquitto-bus-display-password"
  ];
}
