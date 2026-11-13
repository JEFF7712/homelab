{
  config,
  lib,
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
    ../../modules/npm-cache-proxy.nix
    ../../modules/ledfx-bedroom.nix
    ../../modules/bedroom-bluetooth.nix
    ../../modules/forgejo.nix
    ../../modules/nas-s3.nix
    ../../modules/niks3-cache.nix
    ../../modules/tofu-state.nix
    ../../modules/platform-backup.nix
    ../../modules/nas-syncthing.nix
    ../../modules/nas-backup-source.nix
  ];

  networking.hostName = "nas-01";

  sops.defaultSopsFile = ../../../secrets/hosts/nas-01.sops.yaml;

  # Bedroom-only LedFx. It analyses the Bluetooth tap that carries bedroom
  # playback and streams to the relay. Deliberately separate from the
  # shared-spaces LedFx instance: no shared device, virtual, or scene.
  homelab.ledfxBedroom.enable = true;

  # HTTP bridge to connect and monitor the bedroom Bose SoundLink Flex 2 Bluetooth speaker.
  homelab.bedroomBluetooth.enable = true;

  homelab.forgejo = {
    enable = true;
    acmeEmail = "rupanpandyan@gmail.com";
  };
  homelab.s3.enable = true;
  homelab.niks3Cache = {
    enable = true;
    address = "10.0.30.20";
  };
  homelab.tofuState.enable = true;
  homelab.platformBackup.enable = true;
  homelab.platformBackup.bookshelfConfigPath = "/tank/cluster/media-bookshelf-config-pvc-ba9f64f7-e62f-4d69-9f48-a0715b54d396";
  homelab.platformBackup.homeAssistantConfigPath = "/tank/cluster/home-assistant-home-assistant-config-pvc-bc354a6f-5a1f-4a0e-85fd-13bbb74f666f";
  homelab.platformBackup.couchdbDataPath = "/tank/cluster/obsidian-couchdb-data-pvc-ddbae8df-c945-4ec2-a680-0f5127be3c0f";
  homelab.syncthing.enable = true;

  services.homelab-zot-registry = {
    enable = true;
    acmeEmail = "rupanpandyan@gmail.com";
  };

  services.homelab-npm-cache = {
    enable = true;
    acmeEmail = "rupanpandyan@gmail.com";
  };

  fileSystems =
    lib.mapAttrs' (_: dataset: lib.nameValuePair dataset.mountpoint { options = [ "nofail" ]; }) (
      lib.filterAttrs (_: dataset: dataset.mountpoint != null) config.disko.devices.zpool.tank.datasets
    )
    // {
      "/var/log".neededForBoot = true;
    };

  # Bluetooth support for bedroom Bose SoundLink Flex 2
  hardware.bluetooth = {
    enable = true;
    powerOnBoot = true;
    settings = {
      General = {
        # Without this the adapter is not bondable (Pairable: no), so BlueZ
        # pairs without storing a link key and forgets the speaker on every
        # disconnect; reconnecting then needs a fresh pairing.
        AlwaysPairable = true;
        Experimental = true;
        FastConnectable = true;
      };
      Policy = {
        AutoEnable = true;
        ReconnectAttempts = 7;
        ReconnectIntervals = "1, 2, 4, 8, 16, 32, 64";
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
                  "session.suspend-timeout-seconds" = 0;
                };
              };
            }
          ];
        };
        "11-bluetooth-policy" = {
          "monitor.bluez.properties" = {
            "bluez5.auto-connect" = [ "a2dp_sink" ];
          };
        };
      };
    };
  };

  # pipewire-pulse must reload the null-sink module when its generated config
  # changes; otherwise Shairport can start before the virtual sink exists.
  # The null sink lives in the main daemon, so a pipewire restart deletes it
  # and pipewire-pulse never recreates it on its own: partOf restarts pulse,
  # and through their own partOf every bedroom audio consumer, with it. The
  # main daemon also restarts on config changes so module arguments (such as
  # the RTP receiver's) take effect on deploy instead of at the next reboot.
  systemd.services.pipewire.restartTriggers = [ config.environment.etc.pipewire.source ];
  systemd.services.pipewire-pulse = {
    restartTriggers = [ config.environment.etc.pipewire.source ];
    partOf = [ "pipewire.service" ];
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

  # Open firewall ports for Wyoming protocol and mDNS discovery.
  networking.firewall.extraInputRules = ''
    ip saddr { 10.0.0.0/16, 10.42.0.0/16, 100.64.0.0/10 } tcp dport 10700 accept
    ip saddr { 10.0.0.0/16, 10.42.0.0/16, 100.64.0.0/10 } udp dport 5353 accept
  '';

  # AirPlay (shairport-sync) for Music Assistant and phone streaming to Bose speaker
  services.shairport-sync = {
    enable = true;
    openFirewall = true;
    package = pkgs.shairport-sync.overrideAttrs (old: {
      patches = (old.patches or [ ]) ++ [
        (pkgs.writeText "shairport-sync-pa-cork-delay.patch" ''
          --- a/audio_pa.c
          +++ b/audio_pa.c
          @@ -639,31 +639,29 @@
           }
           
           int pa_delay(long *the_delay) {
          -  // debug(1, "pa delay");
             check_pa_stream_status(stream, "audio_pa delay.");
          -  // debug(1,"pa_delay");
             long result = 0;
          -  int reply = 0;
          -  pa_usec_t latency;
          -  int negative;
          +  pa_usec_t latency = 0;
          +  int negative = 0;
          +  pa_sps_t *format_info =
          +      sps_format_lookup(FORMAT_FROM_ENCODED_FORMAT(current_encoded_output_format));
             pa_threaded_mainloop_lock(mainloop);
          -  int gl = pa_stream_get_latency(stream, &latency, &negative);
          +  int corked = (stream != NULL) ? pa_stream_is_corked(stream) : 1;
          +  int gl = 0;
          +  if (!corked) {
          +    gl = pa_stream_get_latency(stream, &latency, &negative);
          +  }
             pa_threaded_mainloop_unlock(mainloop);
          -  if (gl == -PA_ERR_NODATA) {
          -    reply = -ENODEV;
          -  } else if (gl != 0) {
          -    reply = -EIO;
          -  } else {
          -    pa_sps_t *format_info =
          -        sps_format_lookup(FORMAT_FROM_ENCODED_FORMAT(current_encoded_output_format));
          -    // convert audio_occupancy bytes to frames and latency microseconds into frames
          +  if (corked || gl == 0) {
               result = (audio_occupancy / (format_info->bytes_per_sample *
                                            CHANNELS_FROM_ENCODED_FORMAT(current_encoded_output_format))) +
                        (latency * RATE_FROM_ENCODED_FORMAT(current_encoded_output_format)) / 1000000;
          -    reply = 0;
          +  } else {
          +    result = (audio_occupancy / (format_info->bytes_per_sample *
          +                                 CHANNELS_FROM_ENCODED_FORMAT(current_encoded_output_format)));
             }
             *the_delay = result;
          -  return reply;
          +  return 0;
           }
           
           static void flush(void) {
        '')
      ];
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
        audio_backend_buffer_desired_length_in_seconds = 0.5;
        # Left unset this resolves to a null mDNS backend, voluntary RAOP only.
        # iOS discovers the speaker through _airplay._tcp, so the phone sees a
        # cached device and then hangs on "connecting".
        mdns_backend = "avahi";
      };
      sessioncontrol = {
        allow_session_interruption = "yes";
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
    };
    # Settings live in a generated file, so a settings-only change does not
    # otherwise restart the unit and the new config is ignored until reboot.
    restartTriggers = [ config.environment.etc."shairport-sync.conf".source ];
  };

  systemd.services.bedroom-audio-fft = {
    description = "Stream audio FFT from bedroom music tap to LED matrix";
    after = [
      "pipewire.service"
      "pipewire-pulse.service"
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
      exec ${pkgs.pipewire}/bin/pw-record \
        --target=bluez_output.E4_58_BC_10_CA_C9.1 \
        -P '{"stream.capture.sink":true}' \
        --raw --rate=22050 --channels=1 --format=s16 - \
        | ${
          pkgs.python3.withPackages (ps: [ ps.numpy ])
        }/bin/python3 ${../../modules/bedroom-audio-fft.py}
    '';
    serviceConfig = {
      User = "pipewire";
      Group = "pipewire";
      Restart = "always";
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
