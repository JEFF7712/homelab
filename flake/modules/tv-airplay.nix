{
  config,
  lib,
  pkgs,
  ...
}:
let
  user = config.homelab.kiosk.user;
  receiver = pkgs.writeShellScript "tv-airplay" ''
    ${pkgs.procps}/bin/pkill -u ${user} -x .uxplay-wrapped || true
    for attempt in {1..100}; do
      if ! ${pkgs.procps}/bin/pgrep -u ${user} -x .uxplay-wrapped >/dev/null; then
        break
      fi
      ${pkgs.coreutils}/bin/sleep 0.1
    done
    if ${pkgs.procps}/bin/pgrep -u ${user} -x .uxplay-wrapped >/dev/null; then
      echo "Previous TV AirPlay receiver did not exit" >&2
      exit 1
    fi
    export GST_PLUGIN_SYSTEM_PATH_1_0=${
      lib.makeSearchPath "lib/gstreamer-1.0" [
        pkgs.gst_all_1.gst-plugins-base
        pkgs.gst_all_1.gst-plugins-good
        pkgs.gst_all_1.gst-plugins-bad
        pkgs.gst_all_1.gst-libav
      ]
    }
    exec ${pkgs.coreutils}/bin/stdbuf -oL -eL ${pkgs.uxplay}/bin/uxplay \
      -n "Living Room TV" -nh -p 7100 -vs ximagesink -avdec
  '';
  startReceiver = pkgs.writeShellScript "start-tv-airplay" ''
    ${pkgs.systemd}/bin/systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR
    exec ${pkgs.systemd}/bin/systemctl --user restart tv-airplay.service
  '';
in
{
  assertions = [
    {
      assertion =
        config.homelab.kiosk.enable
        && config.homelab.kiosk.compositor == "sway"
        && config.homelab.moonlight.enable
        && config.homelab.moonlight.output != null;
      message = "TV AirPlay requires the Sway kiosk and a dedicated Moonlight output";
    }
  ];

  homelab.kiosk.swayExtraConfig = ''
    exec ${startReceiver}
    assign [class="^GStreamer$" instance="^Living Room TV$"] workspace 2
    for_window [class="^GStreamer$" instance="^Living Room TV$"] fullscreen enable, focus
  '';

  systemd.user.services.tv-airplay = {
    description = "Living Room TV AirPlay receiver";
    unitConfig.ConditionUser = user;
    serviceConfig = {
      ExecStart = receiver;
      Restart = "always";
      RestartSec = "2s";
    };
  };

  services.avahi = {
    enable = true;
    allowInterfaces = [ "enp0s31f6" ];
    ipv6 = false;
    publish.enable = true;
    publish.userServices = true;
  };
  networking.firewall.allowedTCPPorts = [
    7100
    7101
    7102
  ];
  networking.firewall.allowedUDPPorts = [
    7100
    7101
    7102
  ];
}
