{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.monero;

  # Storage preflight gates daemon startup and doubles as the daily check.
  # A fresh database (no LMDB data file) means a first sync: require the full
  # official budget as *available* space, so a mostly-full filesystem cannot
  # pass on total capacity alone. An existing database only needs headroom.
  preflight = pkgs.writeShellScript "monero-storage-preflight" ''
    set -eu
    dir=${lib.escapeShellArg cfg.dataDir}
    if [ -f "$dir/lmdb/data.mdb" ]; then
      need=${toString cfg.minFreeGiB}
      context="existing database"
    else
      need=${toString cfg.requiredAvailGiB}
      context="first sync"
    fi
    avail_kb=$(${pkgs.coreutils}/bin/df --output=avail -k "$dir" | ${pkgs.coreutils}/bin/tail -n +2 | ${pkgs.coreutils}/bin/tr -d ' ')
    avail_gib=$(( avail_kb / 1024 / 1024 ))
    if [ "$avail_gib" -lt "$need" ]; then
      echo "monero storage preflight failed: ''${avail_gib} GiB available in $dir, need ''${need} GiB ($context)" >&2
      exit 1
    fi
  '';
in
{
  options.homelab.monero = {
    enable = lib.mkEnableOption "unpruned Monero full node (monerod)";

    dataDir = lib.mkOption {
      type = lib.types.str;
      default = "/persist/monero";
      description = "Directory holding lmdb and monerod.conf. Must live under /persist so it survives reboot on tmpfs-root hosts.";
    };

    p2pPort = lib.mkOption {
      type = lib.types.port;
      default = 18080;
      description = "Clearnet P2P port (TCP). Peer traffic uses TCP only.";
    };

    openP2P = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Accept inbound TCP on p2pPort so the node serves full history to peers.";
    };

    requiredAvailGiB = lib.mkOption {
      type = lib.types.ints.positive;
      default = 625;
      description = "Available-space gate in GiB for a first sync. Official guidance recommends 625 GiB+ of available SSD storage for an unpruned node; the chain was about 250 GiB on 2026-01-20 and keeps growing.";
    };

    minFreeGiB = lib.mkOption {
      type = lib.types.ints.positive;
      default = 50;
      description = "Available-space floor in GiB for an existing database. Breaches fail the preflight and the daily check.";
    };

    cpuQuota = lib.mkOption {
      type = lib.types.str;
      default = "300%";
      description = "systemd CPUQuota for monerod, bounding initial sync on shared hosts.";
    };

    memoryMax = lib.mkOption {
      type = lib.types.str;
      default = "8G";
      description = "systemd MemoryMax for monerod.";
    };

    ioDevice = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "/dev/disk/by-id/nvme-PC_SN810_NVMe_WDC_1024GB_230907801780-part2";
      description = "Partition device holding dataDir (by-id path preferred). Required for bandwidth caps: cgroup I/O limits are enforced per device, independent of the I/O scheduler.";
    };

    readBandwidthMax = lib.mkOption {
      type = lib.types.str;
      default = "250M";
      description = "systemd IOReadBandwidthMax for monerod on ioDevice.";
    };

    writeBandwidthMax = lib.mkOption {
      type = lib.types.str;
      default = "150M";
      description = "systemd IOWriteBandwidthMax for monerod on ioDevice.";
    };

    exporterPort = lib.mkOption {
      type = lib.types.port;
      default = 9101;
      description = "Host node exporter port for Monero host metrics. 9100 is taken by the k3s node-exporter DaemonSet on shared hosts.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.hasPrefix "/persist/" cfg.dataDir;
        message = "homelab.monero.dataDir must live under /persist so the chain survives reboot.";
      }
      {
        assertion = cfg.requiredAvailGiB >= 625;
        message = "homelab.monero.requiredAvailGiB must be at least 625 GiB for an unpruned node.";
      }
      {
        assertion = cfg.ioDevice != null;
        message = "homelab.monero.ioDevice must name the partition holding dataDir so I/O caps are enforceable.";
      }
    ];

    services.monero = {
      enable = true;
      dataDir = cfg.dataDir;
      prune = false;
      rpc.address = "127.0.0.1";
      rpc.port = 18081;
      extraConfig = ''
        p2p-bind-port=${toString cfg.p2pPort}
        check-updates=disabled
        enable-dns-blocklist=1
        out-peers=12
        in-peers=48
      '';
    };

    # Host metrics with the systemd and filesystem collectors enabled, so
    # Prometheus sees monerod unit state and /persist capacity. Scraped as
    # homelab-04-node (see the kube-prometheus-stack scrape config).
    services.prometheus.exporters.node = {
      enable = true;
      port = cfg.exporterPort;
      enabledCollectors = [
        "systemd"
        "filesystem"
      ];
      openFirewall = false;
    };

    networking.firewall.extraInputRules = lib.mkAfter ''
      ${lib.optionalString cfg.openP2P "tcp dport ${toString cfg.p2pPort} accept"}
      ip saddr 10.0.30.0/24 tcp dport ${toString cfg.exporterPort} accept
      ip saddr 10.42.0.0/16 tcp dport ${toString cfg.exporterPort} accept
    '';

    systemd.tmpfiles.rules = [
      "d ${cfg.dataDir} 0750 monero monero -"
    ];

    systemd.services.monero = {
      # A failed preflight exits non-zero and blocks daemon startup. Ordered
      # before the upstream config-generation preStart.
      preStart = lib.mkBefore "${preflight}";
      serviceConfig = {
        CPUQuota = cfg.cpuQuota;
        CPUWeight = 20;
        Nice = 10;
        # The host NVMe scheduler is `none`, which ignores ioprio weights, so
        # IOSchedulingClass/Priority would be decoration. Bandwidth caps are
        # enforced by the cgroup I/O controller regardless of elevator.
        IOReadBandwidthMax = "${cfg.ioDevice} ${cfg.readBandwidthMax}";
        IOWriteBandwidthMax = "${cfg.ioDevice} ${cfg.writeBandwidthMax}";
        MemoryHigh = "6G";
        MemoryMax = cfg.memoryMax;
        TimeoutStopSec = "300s";
        RestartSec = "30s";
      };
    };

    systemd.services.monero-disk-check = {
      description = "Warn when Monero available space is at risk";
      after = [ "local-fs.target" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${preflight}";
      };
    };

    systemd.timers.monero-disk-check = {
      description = "Daily Monero available-space check";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "daily";
        Persistent = true;
      };
    };
  };
}
