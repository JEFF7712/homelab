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
          # Pin the USB SPDIF optical adapter as the default output. It feeds
          # the LG soundbar over Toslink (measured 2026-09-23: node
          # alsa_output.usb-Generic_USB_SPDIF_Adapter_202110200032-00.analog-stereo,
          # USB 0bda:4e27). The usb-*SPDIF* substring survives USB bus-path
          # renames. Placed after the generic onboard rule so it wins the
          # priority for this one node.
          {
            matches = [
              { "node.name" = "~alsa_output.usb-*SPDIF*"; }
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

  # Re-apply ALSA unmute whenever the QuadCast USB microphone is connected/re-enumerated
  services.udev.extraRules = ''
    ACTION=="add", SUBSYSTEM=="sound", KERNEL=="controlC*", ATTRS{idVendor}=="0951", ATTRS{idProduct}=="171d", RUN+="${pkgs.systemd}/bin/systemctl --no-block restart satellite-alsa-restore.service"
  '';

  networking.firewall.extraInputRules = ''
    ip saddr { 10.0.0.0/16, 10.42.0.0/16, 100.64.0.0/10 } tcp dport 6053 accept
    ip saddr { 10.0.0.0/16, 10.42.0.0/16, 100.64.0.0/10 } tcp dport { 8095, 8097, 38801, 38802, 38803, 38804, 38805, 38806, 38807, 38808, 38809, 38810, 38901, 38902, 38903, 38904, 38905, 38906, 38907, 38908, 38909, 38910 } accept
    ip saddr { 10.0.0.0/16, 10.42.0.0/16, 100.64.0.0/10 } udp dport 5353 accept
    # Allow inbound UDP from internal subnets for Music Assistant AirPlay/RAOP timing and streaming
    meta l4proto udp ip saddr { 10.0.0.0/16, 10.42.0.0/16 } accept
  '';

  systemd.services.satellite-alsa-restore = {
    description = "Unmute QuadCast microphone for Jarvis satellite";
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
      # Keep the QuadCast capture path unmuted for the satellite.
      # Select the mic card by its stable ALSA ID, not its USB-dependent index.
      for card in $(awk -F'[][]' '/[Qq]uad[Cc]ast|[Hh]yper[Xx]/ { gsub(/ /, "", $2); print $2 }' /proc/asound/cards); do
        if amixer -c "$card" scontrols | grep -Fq "Simple mixer control 'Mic',0"; then
          amixer -c "$card" set Mic unmute 100%
        fi
      done
    '';
  };
}
