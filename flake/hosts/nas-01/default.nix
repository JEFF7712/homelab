{
  config,
  pkgs,
  ...
}:
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
    extraConfig.pipewire-pulse."20-bedroom-music-pre-delay" = {
      "pulse.cmd" = [
        {
          cmd = "load-module";
          args = "module-null-sink sink_name=bedroom_music_pre_delay sink_properties=device.description=Bedroom_Music_Pre_Delay";
        }
      ];
    };
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

  # pipewire-pulse must reload the null-sink module when its generated config
  # changes; otherwise Shairport can start before the virtual sink exists.
  systemd.services.pipewire-pulse.restartTriggers = [ config.environment.etc.pipewire.source ];

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
    # Play through Pulse, not native PipeWire. Native pw-play cannot obtain a
    # target for the Bluetooth sink ("no target node available"), which left
    # Music Assistant streaming into nothing; the PulseAudio backend negotiates
    # through pipewire-pulse and does reach the speaker. pacat reads the same
    # raw s16le mono stream from stdin that pw-play did.
    sound.command = "pacat --playback --rate 22050 --channels 1 --format s16le";
    vad.enable = false;
  };

  systemd.services.wyoming-satellite = {
    path = [
      pkgs.pipewire
      pkgs.alsa-utils
      # pacat lives in pulseaudio, not alsa-utils.
      pkgs.pulseaudio
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
        # Left unset this resolves to a null mDNS backend, which advertises only
        # _raop._tcp. iOS discovers the speaker through _airplay._tcp, so the
        # phone sees a cached device and then hangs on "connecting".
        mdns_backend = "avahi";
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

  systemd.services.shairport-sync = {
    environment = {
      PIPEWIRE_RUNTIME_DIR = "/run/pipewire";
      PULSE_SERVER = "unix:/run/pulse/native";
      PULSE_SINK = "bedroom_music_pre_delay";
    };
    # Settings live in a generated file, so a settings-only change does not
    # otherwise restart the unit and the new config is ignored until reboot.
    restartTriggers = [ config.environment.etc."shairport-sync.conf".source ];
  };

  systemd.services.bedroom-audio-delay = {
    description = "Delay bedroom music playback to match LedFx lights";
    after = [
      "pipewire.service"
      "pipewire-pulse.service"
      "bluetooth.target"
    ];
    requires = [
      "pipewire.service"
      "pipewire-pulse.service"
    ];
    partOf = [ "pipewire-pulse.service" ];
    wantedBy = [
      "multi-user.target"
      "pipewire-pulse.service"
    ];
    restartTriggers = [ config.environment.etc.pipewire.source ];
    environment.PIPEWIRE_RUNTIME_DIR = "/run/pipewire";
    script = ''
      exec ${pkgs.pipewire}/bin/pw-loopback \
        --name=bedroom-music-delay \
        --capture=bedroom_music_pre_delay \
        --capture-props='{"stream.capture.sink":true}' \
        --playback=bluez_output.E4_58_BC_10_CA_C9.1 \
        --delay=0.45
    '';
    serviceConfig = {
      User = "pipewire";
      Group = "pipewire";
      Restart = "on-failure";
      RestartSec = "2s";
    };
  };

  environment.systemPackages = with pkgs; [
    alsa-utils
    bluez
    bluez-tools
    pipewire
    wireplumber
  ];
}
