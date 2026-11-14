{
  config,
  lib,
  pkgs,
  fleetInventory,
  ...
}:
let
  cfg = config.homelab.workshopDisplay;
  python = pkgs.python3.withPackages (ps: [ ps.qrcode ]);
  airplay = pkgs.writeShellScript "workshop-airplay" ''
    export GST_PLUGIN_SYSTEM_PATH_1_0=${
      lib.makeSearchPath "lib/gstreamer-1.0" [
        pkgs.gst_all_1.gst-plugins-base
        pkgs.gst_all_1.gst-plugins-good
        pkgs.gst_all_1.gst-plugins-bad
        pkgs.gst_all_1.gst-libav
      ]
    }
    exec ${pkgs.uxplay}/bin/uxplay -n Workshop -nh -hls -p 7100 -vs "waylandsink fullscreen=true" -avdec
  '';
in
{
  options.homelab.workshopDisplay.enable = lib.mkEnableOption "event-driven workshop kiosk and phone controls";
  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = config.homelab.kiosk.enable && config.homelab.kiosk.compositor == "sway";
        message = "Workshop display requires the Sway kiosk";
      }
    ];
    homelab.kiosk.autoplay = true;
    homelab.kiosk.url = "http://127.0.0.1:8094";
    homelab.kiosk.swayExtraConfig = ''
      exec ${airplay}
      for_window [app_id="(?i).*uxplay.*"] fullscreen enable
    '';
    systemd.services.sway-tty1.after = [ "workshop-display.service" ];
    services.avahi = {
      enable = true;
      publish.enable = true;
      publish.userServices = true;
    };
    # Fixed UxPlay ports: three consecutive TCP and UDP ports.
    networking.firewall.allowedTCPPorts = [
      8094
      7100
      7101
      7102
    ];
    networking.firewall.allowedUDPPorts = [
      7100
      7101
      7102
    ];
    systemd.services.workshop-display = {
      description = "Workshop dashboard and paired phone controls";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        ExecStartPost = "${pkgs.curl}/bin/curl --fail --retry 30 --retry-connrefused --retry-delay 1 http://127.0.0.1:8094/health";
        DynamicUser = true;
        LoadCredential = [ "ha-token:${config.sops.secrets.kiosk-ha-token.path}" ];
        Restart = "always";
        RestartSec = "3s";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
        ];
      };
      script = ''
        exec ${python}/bin/python3 ${./workshop-display.py} \
          --ha http://10.0.40.13:8123 \
          --token-file "$CREDENTIALS_DIRECTORY/ha-token" \
          --assets ${./workshop-display} --control-url http://${
            fleetInventory.hosts.${config.networking.hostName}.address
          }:8094
      '';
    };
  };
}
