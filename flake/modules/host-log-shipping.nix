# Ships the systemd journal of a host that is not a k3s node to the
# in-cluster Loki, so the journal alert rules (gitops/observability/loki/
# rules-configmap.yaml) cover it. The Alloy pipeline mirrors the DaemonSet in
# gitops/observability/alloy/release.yaml: same component names (Loki derives
# job="loki.source.journal.journal" from them) and the same labels, with
# hostname taken from the journal's _HOSTNAME field.
{
  config,
  lib,
  fleetInventory,
  ...
}:
let
  cfg = config.homelab.logShipping;

  alloyString = builtins.toJSON;
  labelMap = lib.concatStringsSep ", " (
    lib.mapAttrsToList (name: value: "${name} = ${alloyString value}") cfg.labels
  );
  dropRule = lib.optionalString (cfg.dropUnits != [ ]) ''
    rule {
      source_labels = ["__journal__systemd_unit"]
      regex         = ${alloyString "(${lib.concatMapStringsSep "|" lib.escapeRegex cfg.dropUnits})"}
      action        = "drop"
    }
  '';
in
{
  options.homelab.logShipping = {
    enable = lib.mkEnableOption "shipping this host's systemd journal to the in-cluster Loki";

    lokiAddress = lib.mkOption {
      type = lib.types.str;
      # loki-push LoadBalancer VIP (gitops/observability/loki/push-service.yaml).
      default = "10.0.40.17";
      description = "IPv4 address of the Loki push endpoint; the unit may connect only here.";
    };

    lokiNextHops = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      # The k3s servers: they peer BGP and each runs one loki-gateway replica
      # (gitops/observability/loki/release.yaml).
      default = map (name: fleetInventory.hosts.${name}.address) [
        "homelab-01"
        "homelab-02"
        "homelab-03"
      ];
      description = ''
        Nodes the push VIP is routed to directly on the shared VLAN. Through
        OPNsense the path is asymmetric (the node answers on the VLAN), pf
        never sees the replies and stalls each TCP stream after about 64 KB.
      '';
    };

    lokiUrl = lib.mkOption {
      type = lib.types.str;
      default = "http://${cfg.lokiAddress}/loki/api/v1/push";
      defaultText = lib.literalExpression ''"http://''${lokiAddress}/loki/api/v1/push"'';
      description = "Loki push URL.";
    };

    labels = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = {
        component = "system-journal";
      };
      description = "Static labels on every journal entry, matching the k3s node DaemonSet.";
    };

    dropUnits = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "noisy.service" ];
      description = ''
        systemd units whose entries are dropped on the host. Use only for a
        unit that floods Loki; kernel, ZFS, smartd, sshd and authentication
        entries must keep flowing for the alert rules.
      '';
    };

    goMemLimit = lib.mkOption {
      type = lib.types.str;
      default = "160MiB";
      description = "GOMEMLIMIT soft limit for the Go runtime.";
    };

    memoryHigh = lib.mkOption {
      type = lib.types.str;
      default = "256M";
      description = "systemd MemoryHigh (throttle point) for the unit.";
    };

    memoryMax = lib.mkOption {
      type = lib.types.str;
      default = "384M";
      description = "systemd MemoryMax (hard limit) for the unit.";
    };
  };

  config = lib.mkIf cfg.enable {
    services.alloy = {
      enable = true;
      extraFlags = [
        "--server.http.listen-addr=127.0.0.1:12345"
        # Journal read positions live here; persisted below so a restart
        # resumes instead of re-reading and duplicating history.
        "--storage.path=/var/lib/alloy/data"
        "--disable-reporting"
      ];
    };

    # Equal-cost route to the push VIP across the gateway nodes, hashed per
    # connection, skipping a node whose neighbour entry has failed. Both
    # hosts name their primary network "30-infrastructure".
    systemd.network.networks."30-infrastructure".routes = [
      {
        Destination = "${cfg.lokiAddress}/32";
        MultiPathRoute = map (
          hop: "${hop}@${config.systemd.network.networks."30-infrastructure".matchConfig.Name} 1"
        ) cfg.lokiNextHops;
      }
    ];
    boot.kernel.sysctl = {
      "net.ipv4.fib_multipath_hash_policy" = 1;
      "net.ipv4.fib_multipath_use_neigh" = 1;
    };

    environment.etc."alloy/config.alloy".text = ''
      logging {
        level = "info"
      }

      loki.source.journal "journal" {
        path          = "/var/log/journal"
        relabel_rules = discovery.relabel.journal.rules
        forward_to    = [loki.write.local.receiver]
        labels        = { ${labelMap} }
      }

      discovery.relabel "journal" {
        targets = []

        rule {
          source_labels = ["__journal__hostname"]
          target_label  = "hostname"
        }

        rule {
          source_labels = ["__journal__systemd_unit"]
          target_label  = "unit"
        }

        rule {
          source_labels = ["__journal_priority_keyword"]
          target_label  = "level"
        }

        rule {
          source_labels = ["__journal__transport"]
          target_label  = "transport"
        }
      ${dropRule}
      }

      loki.write "local" {
        endpoint {
          url = ${alloyString cfg.lokiUrl}
        }
      }
    '';

    # A static account instead of the upstream DynamicUser, so the persisted
    # state directory keeps a stable owner across reboots on the tmpfs root.
    users.users.alloy = {
      isSystemUser = true;
      group = "alloy";
      description = "Grafana Alloy journal shipper";
    };
    users.groups.alloy = { };

    systemd.services.alloy = {
      environment.GOMEMLIMIT = cfg.goMemLimit;
      serviceConfig = {
        DynamicUser = lib.mkForce false;
        User = "alloy";
        Group = "alloy";
        # Upstream restarts after 2s; five quick crashes would trip the start
        # limit and leave a failed unit, which blocks later fleet deploys.
        RestartSec = lib.mkForce 10;

        MemoryHigh = cfg.memoryHigh;
        MemoryMax = cfg.memoryMax;
        CPUQuota = "25%";

        # Egress only to loopback and the Loki push address.
        IPAddressDeny = "any";
        IPAddressAllow = [
          "localhost"
          cfg.lokiAddress
        ];

        CapabilityBoundingSet = "";
        AmbientCapabilities = "";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectKernelLogs = true;
        ProtectControlGroups = true;
        ProtectClock = true;
        ProtectHostname = true;
        ProtectProc = "invisible";
        RestrictNamespaces = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        LockPersonality = true;
        MemoryDenyWriteExecute = true;
        RemoveIPC = true;
        RestrictAddressFamilies = [
          "AF_UNIX"
          "AF_INET"
          "AF_INET6"
          "AF_NETLINK"
        ];
        SystemCallArchitectures = "native";
        SystemCallFilter = [
          "@system-service"
          "~@privileged"
        ];
        UMask = "0077";
      };
    };

    environment.persistence."/persist".directories = [
      {
        directory = "/var/lib/alloy";
        user = "alloy";
        group = "alloy";
        mode = "0700";
      }
    ];
  };
}
