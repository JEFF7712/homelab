{
  config,
  pkgs,
  fleetInventory,
  ...
}:
{
  services.zigbee2mqtt = {
    enable = true;
    settings = {
      version = 5;
      homeassistant.enabled = true;
      permit_join = false;
      mqtt = {
        server = "mqtt://${fleetInventory.hosts.adguard-netbird-01.address}:1883";
        user = "!secret.yaml mqtt_user";
        password = "!secret.yaml mqtt_password";
      };
      serial = {
        adapter = "ember";
        port = "/dev/serial/by-id/usb-SONOFF_SONOFF_Dongle_Lite_MG21_048030cb64a2ef11b809926661ce3355-if00-port0";
      };
      advanced = {
        channel = 25;
        network_key = "!secret.yaml network_key";
      };
    };
  };

  sops.secrets.zigbee2mqtt-network-key = { };

  # JSON is valid YAML. The password is a URL-safe token and the network key a
  # JSON byte list, so the placeholders need no escaping.
  sops.templates."zigbee2mqtt-secret.yaml" = {
    content = ''
      {"mqtt_user": "zigbee2mqtt", "mqtt_password": "${
        config.sops.placeholder."mosquitto-zigbee2mqtt-password"
      }", "network_key": ${config.sops.placeholder.zigbee2mqtt-network-key}}
    '';
    restartUnits = [ "zigbee2mqtt-secrets.service" ];
  };

  # Zigbee2MQTT resolves !secret.yaml inside its state directory, so the
  # rendered file is copied there for the service user.
  systemd.services.zigbee2mqtt-secrets = {
    before = [ "zigbee2mqtt.service" ];
    requiredBy = [ "zigbee2mqtt.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      ${pkgs.coreutils}/bin/install -m 0600 -o zigbee2mqtt -g zigbee2mqtt \
        ${config.sops.templates."zigbee2mqtt-secret.yaml".path} /var/lib/zigbee2mqtt/secret.yaml
    '';
  };

  environment.persistence."/persist".directories = [
    "/var/lib/zigbee2mqtt"
  ];
}
