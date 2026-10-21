{ pkgs, ... }:
let
  # Slow ambient music colour for the four shared Zigbee bulbs. The shared
  # LedFx instance on homelab-05 streams one OSC device (pixel_count 4, "All To
  # One" to /shared on UDP 9001) here; this daemon averages each pixel over a
  # 2.5s window and publishes one Zigbee2MQTT colour command per bulb with a
  # matching transition. Zigbee cannot carry per-frame output, so real-time
  # reactiveness is left to a WLED strip driven by LedFx directly.
  #
  # Pixel order must match the LedFx virtual segment order: kitchen ceiling 1,
  # kitchen ceiling 2, living room floor lamp, living room tulip lamp. Devices
  # are Zigbee2MQTT friendly names (the IEEE addresses until renamed there).
  #
  # The relay reuses the zigbee2mqtt broker credential, whose ACL already
  # covers zigbee2mqtt/#, to avoid another mosquitto user plus secret rotation.
  relayPython = pkgs.python3.withPackages (ps: [
    ps.paho-mqtt
    ps.python-osc
    ps.pyyaml
  ]);
  relayDaemon = pkgs.writeTextFile {
    name = "ledfx-zigbee-bridge";
    executable = true;
    destination = "/bin/ledfx-zigbee-bridge";
    text = builtins.readFile ./ledfx-zigbee-bridge.py;
  };
  relayConfig = pkgs.writeText "ledfx-zigbee-bridge.yaml" ''
    pixels:
      - device: "0xb4e8428fe2a50000"
      - device: "0xb4e8428fcc940000"
      - device: "0xb4e84291785a0000"
      - device: "0xb4e84290119a0000"
  '';
in
{
  users.users.ledfx-zigbee-bridge = {
    isSystemUser = true;
    group = "ledfx-zigbee-bridge";
    description = "LedFx OSC to Zigbee2MQTT ambient relay";
  };
  users.groups.ledfx-zigbee-bridge = { };

  systemd.services.ledfx-zigbee-bridge-secrets = {
    before = [ "ledfx-zigbee-bridge.service" ];
    requiredBy = [ "ledfx-zigbee-bridge.service" ];
    unitConfig.ConditionPathExists = [
      "/persist/secrets/mosquitto-zigbee2mqtt-password"
    ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      ${pkgs.coreutils}/bin/install -d -m 0700 -o ledfx-zigbee-bridge -g ledfx-zigbee-bridge /var/lib/ledfx-zigbee-bridge
      {
        echo "MQTT_HOST=10.0.30.10"
        echo "MQTT_PORT=1883"
        echo "MQTT_USER=zigbee2mqtt"
        ${pkgs.coreutils}/bin/printf 'MQTT_PASS='
        ${pkgs.coreutils}/bin/cat /persist/secrets/mosquitto-zigbee2mqtt-password
      } > /var/lib/ledfx-zigbee-bridge/mqtt.env
      ${pkgs.coreutils}/bin/chown ledfx-zigbee-bridge:ledfx-zigbee-bridge /var/lib/ledfx-zigbee-bridge/mqtt.env
      ${pkgs.coreutils}/bin/chmod 0600 /var/lib/ledfx-zigbee-bridge/mqtt.env
    '';
  };

  systemd.services.ledfx-zigbee-bridge = {
    after = [
      "network-online.target"
      "mosquitto.service"
    ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      User = "ledfx-zigbee-bridge";
      EnvironmentFile = "/var/lib/ledfx-zigbee-bridge/mqtt.env";
      ExecStart = "${relayPython}/bin/python3 -u ${relayDaemon}/bin/ledfx-zigbee-bridge --config ${relayConfig} --osc-port 9001 --osc-path /shared --window 2.5 --min-brightness 60 --delta 10";
      Restart = "always";
      RestartSec = "5s";
      NoNewPrivileges = true;
      PrivateTmp = true;
    };
  };
}
