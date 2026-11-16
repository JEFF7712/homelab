{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.persistCapacity;
  gib = 1024 * 1024 * 1024;
  textfileDirectory = "/var/lib/persist-usage-metrics";
in
{
  options.homelab.persistCapacity = {
    enable = lib.mkEnableOption "bounded Nix retention and per-directory /persist usage metrics";

    systemGenerations = lib.mkOption {
      type = lib.types.ints.between 3 100;
      default = 20;
      description = ''
        NixOS system generations kept before each garbage collection, however
        old they are. The common 30-day age limit still applies on top.
      '';
    };

    gcDates = lib.mkOption {
      type = lib.types.str;
      default = "daily";
      description = "systemd calendar expression for nix-gc on this host.";
    };

    minFreeGiB = lib.mkOption {
      type = lib.types.ints.positive;
      default = 32;
      description = ''
        nix.settings.min-free in GiB: when free space falls below it during a
        build, the daemon collects unreachable store paths until max-free.
        An emergency floor; the scheduled collection is the regular path.
      '';
    };

    maxFreeGiB = lib.mkOption {
      type = lib.types.ints.positive;
      default = 64;
      description = "nix.settings.max-free in GiB, the target of a min-free collection.";
    };

    usageTrees = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "/persist:2"
        "/nix/store:0"
        "/var/log:0"
      ];
      description = ''
        PATH:DEPTH walks for homelab_path_disk_usage_bytes. Direct children are
        always published; deeper directories only from 1 GiB.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.maxFreeGiB > cfg.minFreeGiB;
        message = "homelab.persistCapacity.maxFreeGiB must exceed minFreeGiB.";
      }
    ];

    # A host whose system closure changes on most commits gains several
    # generations a day; the weekly 30-day sweep in common-base.nix kept 193
    # of them (42.5 GiB of closures) on homelab-04 in November 2026.
    nix.gc.dates = lib.mkForce cfg.gcDates;
    # Non-fatal: a failed unit makes the next switch exit 4 and blocks
    # deployment; homelab_nix_system_generations shows a stalled trim.
    systemd.services.nix-gc.preStart = ''
      ${config.nix.package.out}/bin/nix-env --profile /nix/var/nix/profiles/system \
        --delete-generations +${toString cfg.systemGenerations} \
        || echo "nix-gc: trimming system generations failed; collecting anyway" >&2
    '';
    nix.settings = {
      min-free = cfg.minFreeGiB * gib;
      max-free = cfg.maxFreeGiB * gib;
    };

    # The host node exporter itself is declared by monero.nix (port 9101,
    # scraped as <host>-node); this adds its textfile collector.
    services.prometheus.exporters.node = {
      enabledCollectors = [ "textfile" ];
      extraFlags = [ "--collector.textfile.directory=${textfileDirectory}" ];
    };
    systemd.tmpfiles.rules = [ "d ${textfileDirectory} 0755 root root -" ];

    systemd.services.persist-usage-metrics = {
      description = "Publish per-directory /persist disk usage";
      after = [ "local-fs.target" ];
      path = [ pkgs.coreutils ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = lib.escapeShellArgs (
          [
            "${pkgs.python3}/bin/python3"
            "${./persist_usage.py}"
            "--output"
            textfileDirectory
          ]
          ++ lib.concatMap (tree: [
            "--tree"
            tree
          ]) cfg.usageTrees
        );
        Nice = 19;
        IOSchedulingClass = "idle";
        TimeoutStartSec = "30min";
      };
    };
    systemd.timers.persist-usage-metrics = {
      description = "Two-hourly /persist disk usage walk";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "10min";
        OnCalendar = "*-*-* 00/2:17:00";
        Persistent = true;
      };
    };
  };
}
