{ pkgs, ... }:
let
  # LedFx OSC relay for the bedroom Roku bulbs. LedFx has no device type that
  # speaks the bulbs' encrypted HTTP API, so one OSC device (pixel_count 3,
  # "All To One" to /bedroom on UDP 9000) streams frames here and this daemon
  # republishes them as HA MQTT JSON light commands on roku/light/<slug>/set,
  # where roku-bridge translates to encrypted bulb requests. Pixel order must
  # match the LedFx group-virtual segment order: desk, floor, strip.
  #
  # Slugs are MAC-derived MQTT topic fragments, already visible on the LAN as
  # retained discovery topics, so they live in a plain store config. The relay
  # reuses the roku-bridge broker credential (whose ACL already covers
  # roku/#) to avoid another mosquitto user plus secret rotation.
  relayPython = pkgs.python3.withPackages (ps: [
    ps.paho-mqtt
    ps.python-osc
    ps.pyyaml
  ]);
  relayDaemon = pkgs.writeTextFile {
    name = "ledfx-roku-bridge";
    executable = true;
    destination = "/bin/ledfx-roku-bridge";
    text = builtins.readFile ./ledfx-roku-bridge.py;
  };
  relayConfig = pkgs.writeText "ledfx-roku-bridge.yaml" ''
    pixels:
      - slug: 7C67AB0A83AB
      - slug: 7C67AB1623B7
      - slug: 7C67AB2A0505
  '';
in
{
  users.users.ledfx-roku-bridge = {
    isSystemUser = true;
    group = "ledfx-roku-bridge";
    description = "LedFx OSC to Roku MQTT relay";
  };
  users.groups.ledfx-roku-bridge = { };

  systemd.services.ledfx-roku-bridge-secrets = {
    before = [ "ledfx-roku-bridge.service" ];
    requiredBy = [ "ledfx-roku-bridge.service" ];
    unitConfig.ConditionPathExists = [
      "/persist/secrets/mosquitto-roku-bridge-password"
    ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      ${pkgs.coreutils}/bin/install -d -m 0700 -o ledfx-roku-bridge -g ledfx-roku-bridge /var/lib/ledfx-roku-bridge
      {
        echo "MQTT_HOST=10.0.30.10"
        echo "MQTT_PORT=1883"
        echo "MQTT_USER=roku-bridge"
        ${pkgs.coreutils}/bin/printf 'MQTT_PASS='
        ${pkgs.coreutils}/bin/cat /persist/secrets/mosquitto-roku-bridge-password
      } > /var/lib/ledfx-roku-bridge/mqtt.env
      ${pkgs.coreutils}/bin/chown ledfx-roku-bridge:ledfx-roku-bridge /var/lib/ledfx-roku-bridge/mqtt.env
      ${pkgs.coreutils}/bin/chmod 0600 /var/lib/ledfx-roku-bridge/mqtt.env
    '';
  };

  systemd.services.ledfx-roku-bridge = {
    after = [
      "network-online.target"
      "mosquitto.service"
    ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      User = "ledfx-roku-bridge";
      EnvironmentFile = "/var/lib/ledfx-roku-bridge/mqtt.env";
      ExecStart = "${relayPython}/bin/python3 -u ${relayDaemon}/bin/ledfx-roku-bridge --config ${relayConfig} --osc-port 9000 --osc-path /bedroom";
      Restart = "always";
      RestartSec = "5s";
      NoNewPrivileges = true;
      PrivateTmp = true;
    };
  };
}
