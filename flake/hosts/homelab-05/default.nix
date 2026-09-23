{ pkgs, ... }:
{
  imports = [
    ./hardware-configuration.nix
    ../../modules/common-base.nix
    ../../modules/disko-single-disk.nix
    ../../modules/k3s-server.nix
    ../../modules/nvidia.nix
    ../../modules/kiosk.nix
  ];

  networking.hostName = "homelab-05";

  homelab.disk.device = "/dev/disk/by-id/nvme-KXG70ZNV1T02_NVMe_KIOXIA_1024GB_42MFC3FQFTC5";

  homelab.k3s = {
    enable = true;
    role = "agent";
    primaryInterface = "enp0s31f6";
    nodeIp = "10.0.30.15";
    serverAddress = "https://10.0.30.11:6443";
    tokenFile = "/persist/secrets/k3s-token";
  };

  services.k3s.extraFlags = [
    "--kubelet-arg=system-reserved=cpu=1,memory=2Gi"
    "--kubelet-arg=kube-reserved=cpu=500m,memory=1Gi"
  ];

  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
    wireplumber.extraConfig = {
      "10-device-priorities" = {
        "monitor.alsa.rules" = [
          # Always prioritize the HyperX QuadCast USB microphone as default input.
          # A single QuadCast substring covers the usb-Kingston_HyperX_QuadCast_S
          # node name and survives USB ID renames (e.g. 4100-00 suffix changes).
          {
            matches = [
              { "node.name" = "~alsa_input.*QuadCast.*"; }
            ];
            actions = {
              update-props = {
                "priority.driver" = 3500;
                "priority.session" = 3500;
              };
            };
          }
          # Keep sink monitor nodes from outranking physical microphone inputs.
          {
            matches = [
              { "node.name" = "~.*monitor.*"; }
            ];
            actions = {
              update-props = {
                "priority.driver" = 100;
                "priority.session" = 100;
              };
            };
          }
          # Deprioritize onboard PCI audio input
          {
            matches = [
              { "node.name" = "~alsa_input.pci.*"; }
            ];
            actions = {
              update-props = {
                "priority.driver" = 500;
                "priority.session" = 500;
              };
            };
          }
          # Nvidia HDMI audio (nothing plugged into the T600 mini-DP ports).
          # Keep this narrow: a generic pci.*pro-output-3 pattern would also
          # match onboard 00:1f.3 pro-output-3, which the rules below own.
          {
            matches = [
              { "node.name" = "~alsa_output.pci-0000_01_00.1.*"; }
            ];
            actions = {
              update-props = {
                "priority.driver" = 2000;
                "priority.session" = 2000;
              };
            };
          }
          # Fallback onboard line-out audio
          {
            matches = [
              { "node.name" = "~alsa_output.pci-0000_00_1f.3.*"; }
            ];
            actions = {
              update-props = {
                "priority.driver" = 1000;
                "priority.session" = 1000;
              };
            };
          }
          # Force onboard audio card to pro-audio profile (exposing pro-output-3 for HDMI soundbar)
          {
            matches = [
              { "device.name" = "~alsa_card.pci-0000_00_1f.3"; }
            ];
            actions = {
              update-props = {
                "device.profile" = "pro-audio";
              };
            };
          }
          # Pin the LG soundbar PCM as the default output. Measured
          # 2026-09-18: pro-output-3 is the only onboard PCM reaching the
          # soundbar on HDMI-A-2. Placed after the generic onboard rule so
          # it wins the priority for this one node.
          {
            matches = [
              { "node.name" = "~alsa_output.pci-0000_00_1f.3.pro-output-3"; }
            ];
            actions = {
              update-props = {
                "priority.driver" = 3000;
                "priority.session" = 3000;
              };
            };
          }
          # Deprioritize QuadCast headphone jack so output doesn't route to the mic
          {
            matches = [
              { "node.name" = "~alsa_output.*QuadCast.*"; }
            ];
            actions = {
              update-props = {
                "priority.driver" = 500;
                "priority.session" = 500;
              };
            };
          }
        ];
      };
    };
  };

  homelab.kiosk = {
    enable = true;
    url = "http://10.0.40.13:8123/local/jarvis/index.html?v=13";
    haTokenFile = "/persist/secrets/jarvis-kiosk-ha-token";
    drmDevice = "/dev/dri/card1";
    scaleFactor = "1.0";
    disableOutputs = [ "HDMI-A-2" ];
  };

  networking.firewall.extraInputRules = ''
    ip saddr { 10.0.0.0/16, 10.42.0.0/16, 100.64.0.0/10 } tcp dport 6053 accept
    ip saddr { 10.0.0.0/16, 10.42.0.0/16, 100.64.0.0/10 } tcp dport { 8095, 8097, 38801 } accept
    ip saddr { 10.0.0.0/16, 10.42.0.0/16, 100.64.0.0/10 } udp dport 5353 accept
  '';

  boot.kernelParams = [ "video=HDMI-A-2:e" ];

  systemd.services.satellite-alsa-restore = {
    description = "Unmute onboard audio fallback for Jarvis satellite";
    after = [ "sound.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [
      pkgs.alsa-utils
      pkgs.gawk
    ];
    script = ''
      if [ -e /sys/class/drm/card1-HDMI-A-2/status ]; then
        echo on > /sys/class/drm/card1-HDMI-A-2/status || true
      fi
      # Onboard stays unmuted as the fallback sink (WirePlumber priority 1000)
      # for when the HDMI soundbar is unreachable.
      # Select the onboard card by its stable ALSA ID, not its USB-dependent index.
      if amixer -c PCH scontrols | grep -Fq "Simple mixer control 'Master',0"; then
        amixer -c PCH set Master unmute 100%
      fi
      if amixer -c PCH scontrols | grep -Fq "Simple mixer control 'Headphone',0"; then
        amixer -c PCH set Headphone unmute 100%
      fi

      for card in $(awk -F'[][]' '/[Qq]uad[Cc]ast|[Hh]yper[Xx]/ { gsub(/ /, "", $2); print $2 }' /proc/asound/cards); do
        if amixer -c "$card" scontrols | grep -Fq "Simple mixer control 'Mic',0"; then
          amixer -c "$card" set Mic unmute 100%
        fi
      done
    '';
  };

  systemd.services.satellite-hdmi-audio-clock = {
    description = "Clock HDMI-A-2 for Jarvis soundbar audio";
    after = [ "cage-tty1.service" ];
    wantedBy = [ "cage-tty1.service" ];
    partOf = [ "cage-tty1.service" ];
    serviceConfig = {
      Type = "simple";
      User = "kiosk";
      Restart = "always";
      RestartSec = "5s";
    };
    environment = {
      XDG_RUNTIME_DIR = "/run/user/1001";
      WAYLAND_DISPLAY = "wayland-0";
    };
    path = [
      pkgs.wlr-randr
      pkgs.curl
      pkgs.gawk
    ];
    script = ''
      # Wait in-process for the kiosk browser instead of exiting: with
      # Restart=always an exit would spin a restart every RestartSec while
      # HA/Chromium is down. Sleeping here keeps one quiet process.
      while ! curl -s --max-time 5 http://127.0.0.1:9222/json | grep -q '"title": "JARVIS"'; do
        sleep 5
      done

      # Steady state note: homelab.kiosk.disableOutputs turns HDMI-A-2 off on
      # every cage start so Chromium stays on the primary display; this daemon
      # re-enables it so the soundbar keeps the video clock its audio needs.
      # Keep the physical display at the compositor origin and HDMI-A-2 to its
      # supported soundbar mode. Reapply only after output state drifts.
      while true; do
        if ! wlr-randr 2>/dev/null | awk '
          /^HDMI-A-1 / { output = "monitor"; next }
          /^HDMI-A-2 / { output = "soundbar"; next }
          /^[^ ]/ { output = "" }
          output == "monitor" {
            if (/Enabled: yes/) monitor_enabled = 1
            if (/1920x1080 px, 60\.000000 Hz .*current/) monitor_mode = 1
            if (/Position: 0,0/) monitor_position = 1
          }
          output == "soundbar" {
            if (/Enabled: yes/) soundbar_enabled = 1
            if (/1024x768 px, 60\.004002 Hz .*current/) soundbar_mode = 1
            if (/Position: 0,0/) soundbar_position = 1
          }
          END {
            exit !(monitor_enabled && monitor_mode && monitor_position &&
              soundbar_enabled && soundbar_mode && soundbar_position)
          }
        '; then
          wlr-randr \
            --output HDMI-A-1 --on --mode 1920x1080@60.000000 --pos 0,0 \
            --output HDMI-A-2 --on --mode 1024x768@60.004002 --pos 0,0 \
            2>/dev/null || true
        fi
        sleep 2
      done
    '';
  };
}
