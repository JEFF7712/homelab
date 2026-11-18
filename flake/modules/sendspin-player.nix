{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.sendspin-player;
  uid = toString config.users.users.${cfg.user}.uid;
  # The satellite matches this name to duck the stream during voice turns
  # (MUSIC_STREAM_MATCH in gitops/voice/satellite.yaml).
  streamName = "Sendspin";
in
{
  options.homelab.sendspin-player = {
    enable = lib.mkEnableOption "Sendspin player: Music Assistant's native protocol into the local PipeWire sink";

    user = lib.mkOption {
      type = lib.types.str;
      default = "kiosk";
      description = "System user owning the PipeWire graph to play into.";
    };

    name = lib.mkOption {
      type = lib.types.str;
      description = "Player name shown in Music Assistant and Home Assistant.";
    };

    serverUrl = lib.mkOption {
      type = lib.types.str;
      default = "ws://127.0.0.1:8927/sendspin";
      description = "Music Assistant Sendspin server WebSocket URL.";
    };
  };

  config = lib.mkIf cfg.enable {
    # The client identity under ~/.config/sendspin is Music Assistant's player
    # id; losing it on the ephemeral root would register a new player.
    environment.persistence."/persist".users.${cfg.user}.directories = [
      ".config/sendspin"
    ];

    systemd.services.sendspin-player = {
      description = "Sendspin player (Music Assistant native playback)";
      after = [
        "network-online.target"
        "pipewire.service"
      ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];
      environment = {
        XDG_RUNTIME_DIR = "/run/user/${uid}";
        PIPEWIRE_PROPS = "{ application.name = ${streamName} node.name = ${streamName} media.role = Music }";
        PULSE_PROP = "application.name=${streamName} media.role=music";
      };
      serviceConfig = {
        User = cfg.user;
        # Software volume: Music Assistant's volume scales only this stream,
        # never the shared sink that also carries Jarvis speech.
        ExecStart = lib.escapeShellArgs [
          "${pkgs.sendspin}/bin/sendspin"
          "daemon"
          "--url"
          cfg.serverUrl
          "--name"
          cfg.name
          "--audio-device"
          "default"
          "--hardware-volume"
          "false"
          "--disable-mpris"
        ];
        Restart = "always";
        RestartSec = "3s";
        NoNewPrivileges = true;
      };
    };
  };
}
