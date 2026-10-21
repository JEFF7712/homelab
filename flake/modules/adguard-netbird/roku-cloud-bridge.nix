{ config, pkgs, ... }:
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

  sops.secrets.roku-cloud-bulbs = {
    owner = "roku-cloud-bridge";
    restartUnits = [ "roku-cloud-bridge.service" ];
  };

  # Only a seed: the daemon renews its session into its own state file and
  # reads the seed only when that state is missing or invalid.
  sops.secrets.roku-cloud-cookies.owner = "roku-cloud-bridge";

  sops.templates."roku-cloud-bridge-mqtt.env" = {
    content = ''
      MQTT_HOST=10.0.30.10
      MQTT_PORT=1883
      MQTT_USER=roku-bridge
      MQTT_PASS=${config.sops.placeholder."mosquitto-roku-bridge-password"}
    '';
    restartUnits = [ "roku-cloud-bridge.service" ];
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/roku-cloud-bridge 0700 roku-cloud-bridge roku-cloud-bridge -"
  ];

  systemd.services.roku-cloud-bridge = {
    after = [
      "network-online.target"
      "mosquitto.service"
    ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      User = "roku-cloud-bridge";
      EnvironmentFile = config.sops.templates."roku-cloud-bridge-mqtt.env".path;
      ExecStart = "${cloudBridgePython}/bin/python3 -u ${cloudBridgeDaemon}/bin/roku-cloud-bridge --config ${config.sops.secrets.roku-cloud-bulbs.path} --cookies /var/lib/roku-cloud-bridge/cookies.json --cookie-seed ${config.sops.secrets.roku-cloud-cookies.path}";
      Restart = "always";
      RestartSec = "5s";
      NoNewPrivileges = true;
      PrivateTmp = true;
    };
  };
}
