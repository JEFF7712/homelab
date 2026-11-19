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
  # Relative to the user's home; persisted below.
  settingsDir = ".config/sendspin-player";

  # The client's aiosendspin must match the server's: Music Assistant 2.10.4
  # (gitops/music-assistant/server.yaml) bundles aiosendspin 9.1.1, and the
  # nixpkgs sendspin 9.1.0 on aiosendspin 10.x fails its Noise handshake
  # (HandshakeAbortedError). sendspin 8.0.0 pins aiosendspin ~=9.1.1. Move both
  # with the next Music Assistant upgrade.
  dropVersionPatchHook =
    old: builtins.filter (p: !(lib.hasInfix "version-patch" (p.name or ""))) old.nativeBuildInputs;
  python = pkgs.python3.override {
    packageOverrides = _final: prev: {
      aiosendspin = prev.aiosendspin.overridePythonAttrs (old: rec {
        version = "9.1.1";
        src = pkgs.fetchPypi {
          pname = "aiosendspin";
          inherit version;
          hash = "sha256-Hpoz7Ftty8FrTwPAdYYZW+qoDrLlukJUuXl5rg4OxLc=";
        };
        # The 10.x test suite does not apply to the 9.1.1 sources.
        doCheck = false;
        nativeBuildInputs = dropVersionPatchHook old;
      });
      sendspin = prev.sendspin.overridePythonAttrs (old: rec {
        version = "8.0.0";
        src = pkgs.fetchPypi {
          pname = "sendspin";
          inherit version;
          hash = "sha256-giz68TVlqSDfUTK4RbCqTjl49sV6D/il7jP8BR/JaKI=";
        };
        doCheck = false;
        nativeBuildInputs = dropVersionPatchHook old;
      });
    };
  };
  sendspin = python.pkgs.toPythonApplication python.pkgs.sendspin;
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

    listenPort = lib.mkOption {
      type = lib.types.port;
      default = 8928;
      description = ''
        Loopback port Music Assistant connects to. Must match MA's Sendspin
        manual_discovery_ip_addresses entry (ws://127.0.0.1:<port>/sendspin).
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    # The client identity in settingsDir is Music Assistant's player id; losing
    # it on the ephemeral root would register a new player. Not the default
    # ~/.config/sendspin: 10.x clients wrote a pairing store there that
    # aiosendspin 9.1.1 cannot load (KeyError: 'record_mode_psk_id').
    environment.persistence."/persist".users.${cfg.user}.directories = [
      settingsDir
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
        # Server-initiated: MA dials this listener the moment it starts, where
        # a client dialing MA backs off up to 5 minutes across MA restarts.
        # Loopback only, so nothing else on the LAN can play here.
        ExecStart = lib.escapeShellArgs [
          "${sendspin}/bin/sendspin"
          "daemon"
          "--interface"
          "127.0.0.1"
          "--port"
          (toString cfg.listenPort)
          "--name"
          cfg.name
          "--audio-device"
          "default"
          "--hardware-volume"
          "false"
          "--disable-mpris"
          "--settings-dir"
          "${config.users.users.${cfg.user}.home}/${settingsDir}"
        ];
        Restart = "always";
        RestartSec = "3s";
        NoNewPrivileges = true;
      };
    };
  };
}
