{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.bedroomBluetooth;
  bridgeScript = pkgs.writeText "bedroom-bluetooth-bridge.py" (
    builtins.readFile ./bedroom-bluetooth.py
  );
in
{
  options.homelab.bedroomBluetooth = {
    enable = lib.mkEnableOption "Bedroom Bose Bluetooth HTTP Bridge";
    port = lib.mkOption {
      type = lib.types.port;
      default = 8889;
      description = "TCP port for the Bluetooth bridge HTTP API";
    };
    mac = lib.mkOption {
      type = lib.types.str;
      default = "E4:58:BC:10:CA:C9";
      description = "Bluetooth MAC address of the bedroom speaker";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.bedroom-bluetooth = {
      description = "Bedroom Bose Bluetooth HTTP Bridge";
      after = [ "bluetooth.target" ];
      wants = [ "bluetooth.target" ];
      wantedBy = [ "multi-user.target" ];
      path = [
        pkgs.bluez
      ];
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 -u ${bridgeScript} --port ${toString cfg.port} --mac ${cfg.mac}";
        Restart = "always";
        RestartSec = "5s";
        User = "root";
        Group = "root";
      };
    };

    networking.firewall.extraInputRules = ''
      ip saddr { 10.0.0.0/16, 10.42.0.0/16, 100.64.0.0/10 } tcp dport ${toString cfg.port} accept
    '';
  };
}
