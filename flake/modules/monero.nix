{
  config,
  lib,
  pkgs,
  fleetInventory,
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

  torSocksPort = config.services.tor.client.socksListenAddress.port;
  onionPort = 18084;

  # monerod resolves seed nodes, the DNS blocklist and DNS checkpoints
  # through /etc/resolv.conf (libunbound forwarders and getaddrinfo). With
  # systemd-resolved that is the loopback stub, which forwards upstream as
  # its own user; otherwise the configured IPv4 nameservers.
  dnsResolvers =
    if config.services.resolved.enable then
      [ "127.0.0.53" ]
    else
      lib.filter (ns: !(lib.hasInfix ":" ns)) config.networking.nameservers;

  # Destinations a compromised monerod or tor must not open connections to:
  # this host's loopback services (approvals, Woodpecker gRPC, oplock,
  # kubelet), the LAN, NetBird/CGNAT, link-local, multicast and reserved
  # ranges. Everything else is the public internet.
  nonPublic4 = [
    "0.0.0.0/8"
    "10.0.0.0/8"
    "100.64.0.0/10"
    "127.0.0.0/8"
    "169.254.0.0/16"
    "172.16.0.0/12"
    "192.0.0.0/24"
    "192.168.0.0/16"
    "198.18.0.0/15"
    "224.0.0.0/4"
    "240.0.0.0/4"
  ];
  nonPublic6 = [
    "::/127"
    "::ffff:0:0/96"
    "64:ff9b::/96"
    "fc00::/7"
    "fe80::/10"
    "ff00::/8"
  ];

  # Rate-limited so a misbehaving peer list cannot flood the journal. The
  # prefix makes drops searchable in Loki (kernel transport).
  denyNonPublic = name: ''
    ip daddr @non-public4 limit rate 6/minute burst 5 packets log prefix "${name}-egress-drop: " level warn
    ip daddr @non-public4 drop
    ip6 daddr @non-public6 limit rate 6/minute burst 5 packets log prefix "${name}-egress-drop: " level warn
    ip6 daddr @non-public6 drop
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
      description = ''
        Accept clearnet inbound TCP on p2pPort so the node serves full history
        to peers. When false, the host firewall keeps the port closed, the
        listener binds to loopback only, and monerod stops advertising the
        port (`hide-my-port`). Outbound peers and the Tor onion listener are
        unaffected.
      '';
    };

    tor.enable = lib.mkEnableOption "Tor onion P2P listener for monerod";

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

    memoryHigh = lib.mkOption {
      type = lib.types.str;
      default = "6G";
      description = "systemd MemoryHigh for monerod; reclaim starts here, below memoryMax.";
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

    restrictedRpc = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Expose the wallet-safe restricted RPC listener to LAN clients. The unrestricted listener stays on localhost.";
      };

      port = lib.mkOption {
        type = lib.types.port;
        default = 18089;
        description = "Restricted RPC port for wallet clients.";
      };

      allowedCidrs = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [
          fleetInventory.networks.management.cidr
          fleetInventory.networks.clients.cidr
          "100.64.0.0/10"
        ];
        description = "Client subnets allowed to reach restricted RPC: management, clients, and NetBird VPN. Guest IoT is deliberately excluded.";
      };
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
      environmentFile = lib.mkIf cfg.tor.enable "/run/monero-onion.env";
      extraConfig = ''
        p2p-bind-port=${toString cfg.p2pPort}
        ${lib.optionalString (!cfg.openP2P) ''
          p2p-bind-ip=127.0.0.1
          hide-my-port=1
        ''}
        # Level 0 plus the per-block "BLOCK SUCCESSFULLY ADDED ... HEIGHT n"
        # lines, so Loki shows the node following the chain tip.
        log-level=0,blockchain:INFO
        check-updates=disabled
        enable-dns-blocklist=1
        out-peers=12
        in-peers=48
        ${lib.optionalString cfg.tor.enable ''
          anonymous-inbound=''${MONERO_ONION_ADDRESS}:${toString onionPort},127.0.0.1:${toString onionPort},48
          tx-proxy=tor,127.0.0.1:${toString torSocksPort},disable_noise
        ''}
        ${lib.optionalString cfg.restrictedRpc.enable ''
          rpc-restricted-bind-ip=0.0.0.0
          rpc-restricted-bind-port=${toString cfg.restrictedRpc.port}
          disable-rpc-ban=1
        ''}
      '';
    };

    services.tor = lib.mkIf cfg.tor.enable {
      enable = true;
      client.enable = true;
      relay.onionServices.monero = {
        version = 3;
        map = [
          {
            port = onionPort;
            target = {
              addr = "127.0.0.1";
              port = onionPort;
            };
          }
        ];
      };
    };

    systemd.services.monero-onion-env = lib.mkIf cfg.tor.enable {
      description = "Prepare monerod onion address";
      requires = [ "tor.service" ];
      after = [ "tor.service" ];
      before = [ "monero.service" ];
      serviceConfig.Type = "oneshot";
      script = ''
        set -eu
        hostname=/var/lib/tor/onion/monero/hostname
        for attempt in $(seq 1 30); do
          if [ -s "$hostname" ]; then
            break
          fi
          sleep 1
        done
        test -s "$hostname"
        printf 'MONERO_ONION_ADDRESS=%s\n' "$(cat "$hostname")" > /run/monero-onion.env
        chmod 0644 /run/monero-onion.env
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
      ip saddr ${fleetInventory.networks.infrastructure.cidr} tcp dport ${toString cfg.exporterPort} accept
      ip saddr 10.42.0.0/16 tcp dport ${toString cfg.exporterPort} accept
      ${lib.optionalString cfg.restrictedRpc.enable "ip saddr { ${lib.concatStringsSep ", " cfg.restrictedRpc.allowedCidrs} } tcp dport ${toString cfg.restrictedRpc.port} accept"}
    '';

    # Socket-owner egress filter. monerod and tor accept inbound sessions
    # (onion P2P through tor, LAN wallet RPC), so replies on established
    # connections pass; new connections may go to the public internet and
    # to the few loopback listeners each daemon needs, nowhere else.
    networking.nftables.tables.monero-egress = {
      family = "inet";
      content = ''
        set non-public4 {
          type ipv4_addr
          flags interval
          elements = { ${lib.concatStringsSep ", " nonPublic4} }
        }
        set non-public6 {
          type ipv6_addr
          flags interval
          elements = { ${lib.concatStringsSep ", " nonPublic6} }
        }
        chain output {
          type filter hook output priority filter; policy accept;
          meta skuid monero jump monero
          ${lib.optionalString cfg.tor.enable "meta skuid ${toString config.ids.uids.tor} jump tor"}
        }
        chain monero {
          ct state established,related accept
          ${lib.optionalString cfg.tor.enable "ip daddr 127.0.0.1 tcp dport ${toString torSocksPort} accept"}
          ${lib.optionalString (dnsResolvers != [ ])
            "ip daddr { ${lib.concatStringsSep ", " dnsResolvers} } meta l4proto { tcp, udp } th dport 53 accept"
          }
          ${denyNonPublic "monero"}
        }
        ${lib.optionalString cfg.tor.enable ''
          chain tor {
            ct state established,related accept
            ip daddr 127.0.0.1 tcp dport ${toString onionPort} accept
            ${denyNonPublic "tor"}
          }
        ''}
      '';
    };
    # The build-time `nft --check` sandbox has no monero user.
    networking.nftables.preCheckRuleset = ''
      sed 's/meta skuid monero /meta skuid nobody /' -i ruleset.conf
    '';

    systemd.tmpfiles.rules = [
      "d ${cfg.dataDir} 0750 monero monero -"
    ];

    systemd.services.monero = {
      requires = lib.optionals cfg.tor.enable [ "monero-onion-env.service" ];
      after = lib.optionals cfg.tor.enable [ "monero-onion-env.service" ];
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
        MemoryHigh = cfg.memoryHigh;
        MemoryMax = cfg.memoryMax;
        TimeoutStopSec = "300s";
        RestartSec = "30s";
        # Additions to the upstream sandbox, which already sets
        # ProtectProc=invisible. MemoryDenyWriteExecute stays off: RandomX
        # JIT-compiles into writable+executable pages. AF_NETLINK is dropped:
        # glibc's getaddrinfo falls back when its netlink interface probe
        # fails, and monerod has no other use for it. @resources stays
        # allowed: monerod calls sched_setscheduler (in @resources) during
        # startup and was killed with SIGSYS when it was denied.
        SystemCallArchitectures = "native";
        SystemCallFilter = lib.mkForce [
          "@system-service"
          "~@privileged"
        ];
        RestrictAddressFamilies = lib.mkForce [
          "AF_INET"
          "AF_INET6"
        ];
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
