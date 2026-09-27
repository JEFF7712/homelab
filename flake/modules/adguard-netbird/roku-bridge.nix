{ pkgs, ... }:
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
  rokuBridgeStage = pkgs.writeShellScriptBin "roku-bridge-stage" ''
    ${pkgs.coreutils}/bin/install -d -m 0700 -o roku-bridge -g roku-bridge /var/lib/roku-bridge
    ${pkgs.coreutils}/bin/install -m 0600 -o roku-bridge -g roku-bridge \
      /persist/secrets/roku-bridge-bulbs.yaml /var/lib/roku-bridge/bulbs.yaml
    {
      echo "MQTT_HOST=10.0.30.10"
      echo "MQTT_PORT=1883"
      echo "MQTT_USER=roku-bridge"
      ${pkgs.coreutils}/bin/printf 'MQTT_PASS='
      ${pkgs.coreutils}/bin/cat /persist/secrets/mosquitto-roku-bridge-password
    } > /var/lib/roku-bridge/mqtt.env
    ${pkgs.coreutils}/bin/chown roku-bridge:roku-bridge /var/lib/roku-bridge/mqtt.env
    ${pkgs.coreutils}/bin/chmod 0600 /var/lib/roku-bridge/mqtt.env
  '';
in
{
  users.users.roku-bridge = {
    isSystemUser = true;
    group = "roku-bridge";
    description = "Roku bulb MQTT bridge";
  };
  users.groups.roku-bridge = { };

  systemd.services.roku-bridge-secrets = {
    before = [ "roku-bridge.service" ];
    requiredBy = [ "roku-bridge.service" ];
    unitConfig.ConditionPathExists = [
      "/persist/secrets/roku-bridge-bulbs.yaml"
      "/persist/secrets/mosquitto-roku-bridge-password"
    ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      ${rokuBridgeStage}/bin/roku-bridge-stage
    '';
  };

  # The bulb list is delivered out of band (CI writes /persist/secrets) and the
  # staging oneshot is RemainAfterExit, so a rebuild that does not change the
  # unit would leave the daemon serving the previous bulb list from memory. A
  # bulb moved to the cloud bridge would then stay double-answered on both
  # topics. ExecStartPre re-stages on every start; the deploy job restarts the
  # daemons after writing new secrets.
  systemd.services.roku-bridge = {
    after = [
      "network-online.target"
      "mosquitto.service"
    ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      User = "roku-bridge";
      EnvironmentFile = "/var/lib/roku-bridge/mqtt.env";
      # Re-stage as root on every start so a redeployed bulb list is always the
      # one the daemon reads.
      ExecStartPre = "+${rokuBridgeStage}/bin/roku-bridge-stage";
      ExecStart = "${rokuBridgePython}/bin/python3 -u ${rokuBridgeDaemon}/bin/roku-bridge --config /var/lib/roku-bridge/bulbs.yaml";
      Restart = "always";
      RestartSec = "5s";
      NoNewPrivileges = true;
      PrivateTmp = true;
    };
  };
}
