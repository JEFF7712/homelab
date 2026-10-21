{ config, pkgs, ... }:
let
  # Pinned from github.com/JEFF7712/roku-bulb-local@7b717417f7b2dda0c5d112163b402b0b8fcd6f03
  # (scripts/bridge.py), vendored as ./roku-bridge.py. Verified functionally
  # identical to the previous inline mirror; upstream drift since was only
  # typing, docstrings, and formatting. Re-pin by copying the file and
  # updating this rev; tests/test_roku_bridge_contract.py enforces the hash.
  rokuBridgePython = pkgs.python3.withPackages (ps: [
    ps.pyyaml
    ps.paho-mqtt
    ps.pycryptodome
  ]);
  rokuBridgeDaemon = pkgs.writeTextFile {
    name = "roku-bridge";
    executable = true;
    destination = "/bin/roku-bridge";
    text = builtins.readFile ./roku-bridge.py;
  };
in
{
  users.users.roku-bridge = {
    isSystemUser = true;
    group = "roku-bridge";
    description = "Roku bulb MQTT bridge";
  };
  users.groups.roku-bridge = { };

  sops.secrets.roku-bridge-bulbs = {
    owner = "roku-bridge";
    restartUnits = [ "roku-bridge.service" ];
  };

  sops.templates."roku-bridge-mqtt.env" = {
    content = ''
      MQTT_HOST=10.0.30.10
      MQTT_PORT=1883
      MQTT_USER=roku-bridge
      MQTT_PASS=${config.sops.placeholder."mosquitto-roku-bridge-password"}
    '';
    restartUnits = [ "roku-bridge.service" ];
  };

  systemd.services.roku-bridge = {
    after = [
      "network-online.target"
      "mosquitto.service"
    ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      User = "roku-bridge";
      EnvironmentFile = config.sops.templates."roku-bridge-mqtt.env".path;
      ExecStart = "${rokuBridgePython}/bin/python3 -u ${rokuBridgeDaemon}/bin/roku-bridge --config ${config.sops.secrets.roku-bridge-bulbs.path}";
      Restart = "always";
      RestartSec = "5s";
      NoNewPrivileges = true;
      PrivateTmp = true;
    };
  };
}
