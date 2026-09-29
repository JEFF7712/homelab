{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.jarvis-spectrum;
in
{
  options.homelab.jarvis-spectrum = {
    enable = lib.mkEnableOption "Jarvis soundbar spectrum feed for the kiosk face";

    port = lib.mkOption {
      type = lib.types.port;
      default = 2975;
      description = "Localhost TCP port serving the spectrum SSE stream and health endpoint.";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "kiosk";
      description = "System user owning the PipeWire graph to capture (must match the kiosk user so the sink monitor is reachable).";
    };

    bars = lib.mkOption {
      type = lib.types.ints.positive;
      default = 28;
      description = "FFT bar count per frame. Must match the face renderer.";
    };

    framerate = lib.mkOption {
      type = lib.types.ints.positive;
      default = 20;
      description = "Spectrum frames per second.";
    };

    sinkMatch = lib.mkOption {
      type = lib.types.str;
      default = "SPDIF";
      description = "Substring of the PipeWire sink node name feeding the soundbar. Falls back to cava auto source when unmatched.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.jarvis-spectrum = {
      description = "Jarvis soundbar spectrum feed (cava FFT relay for the kiosk face)";
      after = [ "pipewire.service" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        User = cfg.user;
        # PipeWire/pulse sockets live under the owning user's runtime dir;
        # without this both pw-dump and cava fail to connect (exit 255/1).
        Environment = "XDG_RUNTIME_DIR=/run/user/${toString config.users.users.${cfg.user}.uid}";
        ExecStart = ''
          ${pkgs.python3}/bin/python3 ${./jarvis-spectrum.py} \
            --port ${toString cfg.port} \
            --bars ${toString cfg.bars} \
            --framerate ${toString cfg.framerate} \
            --sink-match ${lib.escapeShellArg cfg.sinkMatch} \
            --cava-bin ${pkgs.cava}/bin/cava \
            --pw-dump-bin ${pkgs.pipewire}/bin/pw-dump
        '';
        Restart = "always";
        RestartSec = "3s";
        NoNewPrivileges = true;
      };
    };
  };
}
