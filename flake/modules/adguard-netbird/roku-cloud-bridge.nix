{ pkgs, ... }:
let
  # Cloud MQTT bridge for Roku bulbs whose firmware closed the LAN port-88
  # server (LS1016X strip on 1.2.1.13). Speaks the same topics/discovery as
  # roku-bridge so HA needs no changes; only list bulbs here that have been
  # REMOVED from the LAN bridge config. Reuses the roku-bridge broker
  # credential (ACL already covers roku/# and homeassistant/#).
  cloudBridgePython = pkgs.python3.withPackages (ps: [
    ps.paho-mqtt
    ps.pyyaml
    ps.requests
    ps.websockets
  ]);
  cloudBridgeDaemon = pkgs.writeTextFile {
    name = "roku-cloud-bridge";
    executable = true;
    destination = "/bin/roku-cloud-bridge";
    text = builtins.readFile ./roku-cloud-bridge.py;
  };
in
{
  users.users.roku-cloud-bridge = {
    isSystemUser = true;
    group = "roku-cloud-bridge";
    description = "Roku bulb cloud MQTT bridge";
  };
  users.groups.roku-cloud-bridge = { };

  systemd.services.roku-cloud-bridge-secrets = {
    before = [ "roku-cloud-bridge.service" ];
    requiredBy = [ "roku-cloud-bridge.service" ];
    unitConfig.ConditionPathExists = [
      "/persist/secrets/roku-cloud-bulbs.yaml"
      "/persist/secrets/roku-cloud-cookies.json"
      "/persist/secrets/mosquitto-roku-bridge-password"
    ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      ${pkgs.coreutils}/bin/install -d -m 0700 -o roku-cloud-bridge -g roku-cloud-bridge /var/lib/roku-cloud-bridge
      ${pkgs.coreutils}/bin/install -m 0600 -o roku-cloud-bridge -g roku-cloud-bridge \
        /persist/secrets/roku-cloud-bulbs.yaml /var/lib/roku-cloud-bridge/bulbs.yaml
      if ! ${pkgs.coreutils}/bin/test -s /var/lib/roku-cloud-bridge/cookies.json; then
        ${pkgs.coreutils}/bin/install -m 0600 -o roku-cloud-bridge -g roku-cloud-bridge \
          /persist/secrets/roku-cloud-cookies.json /var/lib/roku-cloud-bridge/cookies.json
      fi
      {
        echo "MQTT_HOST=10.0.30.10"
        echo "MQTT_PORT=1883"
        echo "MQTT_USER=roku-bridge"
        ${pkgs.coreutils}/bin/printf 'MQTT_PASS='
        ${pkgs.coreutils}/bin/cat /persist/secrets/mosquitto-roku-bridge-password
      } > /var/lib/roku-cloud-bridge/mqtt.env
      ${pkgs.coreutils}/bin/chown roku-cloud-bridge:roku-cloud-bridge /var/lib/roku-cloud-bridge/mqtt.env
      ${pkgs.coreutils}/bin/chmod 0600 /var/lib/roku-cloud-bridge/mqtt.env
    '';
  };

  systemd.services.roku-cloud-bridge = {
    after = [
      "network-online.target"
      "mosquitto.service"
    ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      User = "roku-cloud-bridge";
      EnvironmentFile = "/var/lib/roku-cloud-bridge/mqtt.env";
      ExecStart = "${cloudBridgePython}/bin/python3 -u ${cloudBridgeDaemon}/bin/roku-cloud-bridge --config /var/lib/roku-cloud-bridge/bulbs.yaml --cookies /var/lib/roku-cloud-bridge/cookies.json --cookie-seed /persist/secrets/roku-cloud-cookies.json";
      Restart = "always";
      RestartSec = "5s";
      NoNewPrivileges = true;
      PrivateTmp = true;
    };
  };
}
