{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.ledfxBedroom;

  # Upstream digest, as recorded in registry/images.lock.json and used by the
  # shared-spaces manifest. The local registry stores this image as a converted
  # Docker v2 manifest whose own digest differs from the upstream OCI index, so
  # the upstream digest is not addressable there ("manifest unknown"). The
  # import pipeline republishes it under a retention tag that embeds the
  # upstream digest, which is the stable local handle. tests assert the two
  # stay tied together.
  upstreamDigest = "sha256:a5ff8549a847d1b2a10595a1137cc4e7352f7d29aafb96eff63cef92634b3036";
  image = "registry.rupan.dev/upstream/ghcr.io/ledfx/ledfx:retention-deployed-a5ff8549a847d1b2";

  prepareState = pkgs.writeShellScript "prepare-ledfx-bedroom-state" ''
    set -eu
    umask 0077
    ${pkgs.coreutils}/bin/install -d -m 0700 ${lib.escapeShellArg cfg.stateDir}
    # The image runs as its own ledfx user (uid/gid 1000) and owns the mounted
    # config directory, so it must not stay root-owned or it cannot write
    # ledfx.log and dies on startup.
    ${pkgs.coreutils}/bin/install -d -m 0700 -o 1000 -g 1000 ${lib.escapeShellArg cfg.configDir}
    ${pkgs.coreutils}/bin/install -d -m 0700 ${lib.escapeShellArg cfg.storageDir}
    ${pkgs.coreutils}/bin/install -d -m 0755 ${lib.escapeShellArg cfg.storageDir}/run
    test -s ${lib.escapeShellArg cfg.authFile}
  '';

  # Podman flags shared by every invocation. Under systemd, podman creates a
  # transient unit per container and another per healthcheck run; those
  # transient units linger as "failed" when a container is stopped, which makes
  # switch-to-configuration exit non-zero even though the activation succeeded.
  # Managing the container from our own unit keeps podman out of systemd's unit
  # accounting entirely. Readiness is asserted by the deploy job instead.
  #
  # This must stay a single line: an embedded newline ends the command, and the
  # remaining flags are then parsed as separate commands.
  podmanGlobal = "--root ${lib.escapeShellArg cfg.storageDir} --runroot ${lib.escapeShellArg cfg.storageDir}/run --cgroup-manager=cgroupfs --events-backend=file";

  runContainer = pkgs.writeShellScript "run-ledfx-bedroom" ''
    set -eu
    umask 0077

    # Pull only when the local store lacks it. The registry credential is the
    # same read-only `node` account the k3s nodes use; it is only ever passed
    # via --authfile and never appears in argv.
    if ! ${pkgs.podman}/bin/podman ${podmanGlobal} image exists ${image}; then
      ${pkgs.podman}/bin/podman ${podmanGlobal} pull --authfile ${lib.escapeShellArg cfg.authFile} ${image}
    fi

    exec ${pkgs.podman}/bin/podman ${podmanGlobal} \
      run --rm --name ledfx-bedroom \
      --network host \
      --pull never \
      --no-healthcheck \
      --security-opt no-new-privileges \
      --cap-drop ALL \
      --read-only \
      --tmpfs /tmp:rw,noexec,nosuid,size=64m \
      --memory ${toString cfg.memory} --cpus ${toString cfg.cpus} \
      -v ${lib.escapeShellArg cfg.configDir}:/home/ledfx/ledfx-config \
      -v /run/pipewire:/run/pipewire:ro \
      -v /run/pulse:/run/pulse:ro \
      -e TZ=${cfg.timeZone} \
      -e LEDFX_PORT=${toString cfg.port} \
      -e PULSECLIENTMODE=true \
      -e PULSE_SERVER=unix:/run/pulse/native \
      -e PULSE_SOURCE=${cfg.pulseSource} \
      ${image}
  '';
in
{
  options.homelab.ledfxBedroom = {
    enable = lib.mkEnableOption "Bedroom-scoped LedFx on the NAS, isolated from shared spaces";

    image = lib.mkOption {
      type = lib.types.str;
      default = image;
      description = "Local-registry reference for the shared LedFx build.";
    };

    upstreamDigest = lib.mkOption {
      type = lib.types.str;
      default = upstreamDigest;
      description = ''
        Upstream digest this instance tracks, matching the shared-spaces pin in
        registry/images.lock.json. The local image reference is the retention
        tag that embeds this digest, because the registry stores a converted
        manifest whose own digest differs.
      '';
    };

    pulseSource = lib.mkOption {
      type = lib.types.str;
      default = "bluez_output.E4_58_BC_10_CA_C9.1.monitor";
      description = ''
        PulseAudio source LedFx analyses. This is the monitor of the Bluetooth
        sink that carries all bedroom playback on this host, so the bedroom
        effects follow the bedroom speaker. It is deliberately NOT the shared
        audio tap used by the living-room LedFx instance.
      '';
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8888;
      description = "Host port for the LedFx REST API. Only the k3s nodes may reach it.";
    };

    oscTarget = lib.mkOption {
      type = lib.types.str;
      default = "10.0.30.10:9000";
      description = "relay host:port that receives this instance's OSC frames.";
    };

    oscPath = lib.mkOption {
      type = lib.types.str;
      default = "/bedroom";
      description = "OSC path this instance streams to. Must match the relay's mapping.";
    };

    timeZone = lib.mkOption {
      type = lib.types.str;
      default = "America/Chicago";
    };

    memory = lib.mkOption {
      type = lib.types.str;
      default = "768M";
    };

    cpus = lib.mkOption {
      type = lib.types.str;
      default = "1.5";
    };

    stateDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/ledfx-bedroom";
    };

    configDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/ledfx-bedroom/config";
    };

    storageDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/ledfx-bedroom/containers";
      description = "Podman graph root, kept inside the persisted state directory so the pinned image survives reboot.";
    };

    authFile = lib.mkOption {
      type = lib.types.path;
      default = "/persist/secrets/registry-auth.json";
      description = "Podman authfile granting read on upstream/**. Installed by the deploy job.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.hasPrefix "/persist/" (toString cfg.authFile);
        message = "ledfxBedroom.authFile must live under /persist so it survives reboot.";
      }
    ];

    virtualisation.podman.enable = true;

    # Container graph and LedFx config both live under /persist: the root is
    # tmpfs, so an unpinned image would otherwise be re-pulled on every boot.
    environment.persistence."/persist".directories = [ (toString cfg.stateDir) ];

    networking.firewall.extraInputRules = lib.mkAfter ''
      ip saddr { 10.0.30.11, 10.0.30.12, 10.0.30.13, 10.0.30.14, 10.0.30.15 } tcp dport ${toString cfg.port} accept
    '';

    systemd.services.ledfx-bedroom = {
      after = [
        "network-online.target"
        "pipewire.service"
      ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];
      unitConfig.ConditionPathExists = [ cfg.authFile ];
      serviceConfig = {
        Type = "simple";
        ExecStartPre = "${prepareState}";
        ExecStart = "${runContainer}";
        Restart = "always";
        RestartSec = "10s";
        # Podman needs the container store; nothing else in this unit does.
        ProtectSystem = "false";
        NoNewPrivileges = false;
        PrivateTmp = true;
        UMask = "0077";
      };
    };
  };
}
