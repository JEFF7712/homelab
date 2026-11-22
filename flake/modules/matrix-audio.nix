{
  config,
  lib,
  pkgs,
  fleetInventory,
  ...
}:
let
  cfg = config.homelab.matrixAudio;
  script = pkgs.writeText "matrix-audio.py" (builtins.readFile ./matrix-audio.py);
  nodes = lib.filter (host: host.k3s or false) (lib.attrValues fleetInventory.hosts);
  sources = lib.concatStringsSep ", " ([ "10.42.0.0/16" ] ++ map (host: host.address) nodes);
in
{
  options.homelab.matrixAudio.enable = lib.mkEnableOption "bedroom matrix fireworks audio";
  config = lib.mkIf cfg.enable {
    users.users.matrix-audio = {
      isSystemUser = true;
      group = "pipewire";
      extraGroups = [ "audio" ];
    };
    systemd.services.matrix-audio = {
      description = "Bedroom matrix fireworks audio";
      wantedBy = [ "multi-user.target" ];
      after = [ "pipewire-pulse.service" ];
      requires = [ "pipewire-pulse.service" ];
      partOf = [ "pipewire-pulse.service" ];
      path = [ pkgs.pulseaudio ];
      environment.PULSE_SERVER = "unix:/run/pulse/native";
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 ${script} --samples ${./matrix-audio-samples} --mac ${config.homelab.bedroomBluetooth.mac}";
        User = "matrix-audio";
        Group = "pipewire";
        Restart = "always";
        RestartSec = 2;
        MemoryMax = "128M";
        CPUQuota = "25%";
        TasksMax = 32;
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_UNIX"
        ];
        CapabilityBoundingSet = "";
        UMask = "0077";
      };
    };
    networking.firewall.extraInputRules = ''
      ip saddr { ${sources} } udp dport 8891 accept
      ip saddr { 10.0.0.0/16, 10.42.0.0/16, 100.64.0.0/10 } tcp dport 8891 accept
    '';
  };
}
