{ pkgs, ... }:
{
  imports = [
    ./disk-config.nix
    ./hardware-configuration.nix
    ./tank-config.nix
    ../../modules/common-base.nix
    ../../modules/nas-base.nix
    ../../modules/nas-data.nix
    ../../modules/zot-registry.nix
    ../../modules/ledfx-bedroom.nix
  ];

  networking.hostName = "nas-01";

  # Bedroom-only LedFx. It analyses the Bluetooth tap that carries bedroom
  # playback and streams to the relay. Deliberately separate from the
  # shared-spaces LedFx instance: no shared device, virtual, or scene.
  homelab.ledfxBedroom.enable = true;

  services.homelab-zot-registry = {
    enable = true;
    acmeEmail = "rupanpandyan@gmail.com";
  };

  fileSystems."/var/log".neededForBoot = true;

  # Bluetooth support for bedroom Bose SoundLink Flex 2
  hardware.bluetooth = {
    enable = true;
    powerOnBoot = true;
    settings = {
      General = {
        Enable = "Source,Sink,Media,Socket";
        Experimental = true;
        AutoEnable = true;
      };
    };
  };

  # Persist Bluetooth pairing keys across reboots
  environment.persistence."/persist".directories = [
    "/var/lib/bluetooth"
  ];

  # System-wide audio stack for headless server
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    systemWide = true;
    alsa.enable = true;
    pulse.enable = true;
    wireplumber = {
      enable = true;
      extraConfig = {
        "10-bluetooth-priorities" = {
          "monitor.bluez.rules" = [
            {
              matches = [
                { "node.name" = "~bluez_output.*"; }
              ];
              actions = {
                update-props = {
                  "priority.driver" = 3000;
                  "priority.session" = 3000;
                };
              };
            }
          ];
        };
      };
    };
  };

  # Wyoming satellite for bedroom audio playback (Jarvis + media)
  users.users.wyoming = {
    isSystemUser = true;
    group = "pipewire";
    extraGroups = [ "audio" ];
    description = "Wyoming satellite service user";
  };

  services.wyoming.satellite = {
    enable = true;
    user = "wyoming";
    group = "pipewire";
    name = "Bedroom Satellite";
    area = "Bedroom";
    uri = "tcp://0.0.0.0:10700";
    # In output-only mode, sleep infinity provides an idle mic channel until a physical mic is added
    microphone.command = "sleep infinity";
    # pw-play routes audio to PipeWire default sink (Bluetooth Bose speaker)
    sound.command = "pw-play -a --rate 22050 --channels 1 --format s16 -";
    vad.enable = false;
  };

  systemd.services.wyoming-satellite = {
    path = [
      pkgs.pipewire
      pkgs.alsa-utils
    ];
    environment = {
      PIPEWIRE_RUNTIME_DIR = "/run/pipewire";
      PULSE_SERVER = "unix:/run/pulse/native";
    };
  };

  # Open firewall ports for Wyoming protocol and mDNS discovery
  networking.firewall.extraInputRules = ''
    ip saddr { 10.0.0.0/16, 10.42.0.0/16, 100.64.0.0/10 } tcp dport 10700 accept
    ip saddr { 10.0.0.0/16, 10.42.0.0/16, 100.64.0.0/10 } udp dport 5353 accept
  '';

  # AirPlay (shairport-sync) for Music Assistant and phone streaming to Bose speaker
  services.shairport-sync = {
    enable = true;
    openFirewall = true;
    package = pkgs.shairport-sync.overrideAttrs (old: {
      postPatch = (old.postPatch or "") + ''
        substituteInPlace shairport.c \
          --replace-fail "config.port = 5000;" "if (config.port == 0) config.port = 5000;"
      '';
    });
    arguments = "-p 5002";
    settings = {
      general = {
        name = "Bedroom Speaker";
        port = 5002;
        # The native PipeWire backend cannot open a stream to the Bluetooth sink:
        # the sink node has no target node, so playback fails with "no target node
        # available" and AirPlay sessions connect but never start. The PulseAudio
        # backend negotiates through pipewire-pulse, which does reach the speaker.
        output_backend = "pulseaudio";
      };
      metadata = {
        enabled = "yes";
        include_cover_art = "yes";
      };
    };
  };

  networking.firewall.allowedTCPPorts = [ 5002 ];

  users.users.shairport.extraGroups = [
    "pipewire"
    "audio"
  ];

  systemd.services.shairport-sync.environment = {
    PIPEWIRE_RUNTIME_DIR = "/run/pipewire";
    PULSE_SERVER = "unix:/run/pulse/native";
  };

  environment.systemPackages = with pkgs; [
    alsa-utils
    bluez
    bluez-tools
    pipewire
    wireplumber
  ];
}
