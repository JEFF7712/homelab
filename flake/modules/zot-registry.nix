{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.homelab-zot-registry;

  # Shared with the GC fixture in CI so both run one zot build.
  inherit ((import ../../nix/zot-release.nix { inherit pkgs lib; }))
    zotPackage
    supported
    ;

  baseConfig = pkgs.writeText "zot-base-config.json" (
    builtins.toJSON {
      distSpecVersion = "1.1.1";
      storage = {
        rootDirectory = cfg.storagePath;
        commit = true;
        dedupe = true;
        gc = true;
        gcInterval = "24h";
        fastRestart = cfg.fastRestart;
      };
      http = {
        address = "127.0.0.1";
        port = toString cfg.listenPort;
        realm = cfg.hostName;
        compat = [ "docker2s2" ];
        # Importer uploads stream from upstream at source speed, so multi-GB
        # layers exceed the 60s server default. Listener is loopback-only.
        readTimeout = "30m";
        writeTimeout = "30m";
      };
      log.level = "info";
      extensions = {
        search.enable = true;
        ui.enable = true;
      };
    }
  );

  prepareConfig = pkgs.writeShellScript "prepare-zot-config" ''
    set -eu
    umask 0077

    htpasswd="$CREDENTIALS_DIRECTORY/htpasswd"
    access_control="$CREDENTIALS_DIRECTORY/access-control.json"
    test -s "$htpasswd"
    ${pkgs.jq}/bin/jq -e '
      type == "object" and
      (.repositories | type == "object") and
      (.repositories["**"].defaultPolicy == []) and
      ([.repositories[] | .defaultPolicy == []] | all) and
      ([.repositories[] | has("anonymousPolicy")] | any | not)
      and (.adminPolicy == {
        "users": ["maintenance"],
        "actions": ["read", "create", "update", "delete"]
      })
    ' "$access_control" >/dev/null

    ${pkgs.jq}/bin/jq \
      --arg htpasswd "$htpasswd" \
      --slurpfile accessControl "$access_control" \
      '.http.auth = {
        "failDelay": 5,
        "htpasswd": { "path": $htpasswd }
      } | .http.accessControl = $accessControl[0]' \
      ${baseConfig} > "$RUNTIME_DIRECTORY/config.json.tmp"
    ${pkgs.coreutils}/bin/mv "$RUNTIME_DIRECTORY/config.json.tmp" "$RUNTIME_DIRECTORY/config.json"
  '';

  ensureStoragePermissions = pkgs.writeShellScript "ensure-zot-storage-permissions" ''
    set -eu
    ${pkgs.coreutils}/bin/install -d -m 0750 -o zot -g zot ${lib.escapeShellArg cfg.storagePath}
  '';

  writeMetrics = pkgs.writeShellScript "write-zot-platform-metrics" ''
    set -eu
    output="$STATE_DIRECTORY/zot-platform.prom"
    temporary="$output.tmp"

    {
      certificate="/var/lib/acme/${cfg.hostName}/fullchain.pem"
      if test -r "$certificate"; then
        not_after="$(${pkgs.openssl}/bin/openssl x509 -enddate -noout -in "$certificate")"
        expiry="$(${pkgs.coreutils}/bin/date --date="''${not_after#notAfter=}" +%s)"
        printf '# TYPE homelab_zot_tls_certificate_expiry_timestamp_seconds gauge\n'
        printf 'homelab_zot_tls_certificate_expiry_timestamp_seconds %s\n' "$expiry"
      fi

      for status in backup import; do
        marker="${cfg.statusPath}/$status-last-success"
        if test -r "$marker"; then
          read -r timestamp < "$marker"
          test -n "$timestamp"
          case "$timestamp" in
            *[!0-9]*) exit 1 ;;
          esac
          printf '# TYPE homelab_zot_%s_last_success_timestamp_seconds gauge\n' "$status"
          printf 'homelab_zot_%s_last_success_timestamp_seconds %s\n' "$status" "$timestamp"
        fi
      done
    } > "$temporary"

    ${pkgs.coreutils}/bin/chmod 0644 "$temporary"
    ${pkgs.coreutils}/bin/mv "$temporary" "$output"
  '';
in
{
  options.services.homelab-zot-registry = {
    enable = lib.mkEnableOption "the private homelab zot registry";

    package = lib.mkOption {
      type = lib.types.package;
      default = zotPackage;
      defaultText = lib.literalExpression "the pinned zot 2.1.20 release binary";
      description = "zot package to run.";
    };

    hostName = lib.mkOption {
      type = lib.types.str;
      default = "registry.rupan.dev";
      description = "Private DNS name served by the TLS reverse proxy.";
    };

    listenPort = lib.mkOption {
      type = lib.types.port;
      default = 5000;
      description = "Loopback port used between nginx and zot.";
    };

    storagePath = lib.mkOption {
      type = lib.types.str;
      default = "/tank/registry";
      description = "Mounted local filesystem containing all zot data and metadata.";
    };

    fastRestart = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Skip full storage directory reconciliation on startup when metaDB fast-restart stamp matches.";
    };

    htpasswdFile = lib.mkOption {
      type = lib.types.str;
      default = "/persist/zot/htpasswd";
      description = "Runtime bcrypt htpasswd file. This path is loaded as a systemd credential.";
    };

    accessControlFile = lib.mkOption {
      type = lib.types.str;
      default = "/persist/zot/access-control.json";
      description = "Runtime zot accessControl object. It must deny anonymous and default access.";
    };

    cloudflareTokenFile = lib.mkOption {
      type = lib.types.str;
      default = "/persist/zot/cloudflare-dns-api-token";
      description = "Runtime Cloudflare token used only by the ACME DNS-01 service.";
    };

    acmeEmail = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Contact email for the ACME account.";
    };

    allowedSourceNetworks = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "10.0.10.0/24"
        "10.0.30.0/24"
        "10.42.0.0/16"
        "100.64.0.0/10"
      ];
      description = "IPv4 networks permitted to connect to the registry TLS endpoint.";
    };

    metricsPort = lib.mkOption {
      type = lib.types.port;
      default = 9100;
      description = "Port for the NAS node exporter scrape endpoint.";
    };

    metricsAllowedSourceNetworks = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "10.0.10.0/24"
        "10.0.30.0/24"
        "10.42.0.0/16"
      ];
      description = "Management and cluster networks permitted to scrape node exporter.";
    };

    statusPath = lib.mkOption {
      type = lib.types.str;
      default = "/persist/zot/status";
      description = "Directory containing Unix-timestamp success markers for registry backup and import jobs.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = supported;
        message = "homelab-zot-registry supports only x86_64-linux and aarch64-linux";
      }
      {
        assertion = cfg.acmeEmail != null;
        message = "services.homelab-zot-registry.acmeEmail must be set";
      }
      {
        assertion = lib.hasPrefix "/" cfg.storagePath;
        message = "services.homelab-zot-registry.storagePath must be absolute";
      }
      {
        assertion = cfg.storagePath == "/tank/registry";
        message = "homelab-zot-registry storage must use the tank/registry dataset at /tank/registry";
      }
      {
        assertion = lib.hasPrefix "/" cfg.htpasswdFile && lib.hasPrefix "/" cfg.accessControlFile;
        message = "zot runtime credential paths must be absolute";
      }
      {
        assertion = lib.hasPrefix "/" cfg.cloudflareTokenFile;
        message = "the zot Cloudflare token path must be absolute";
      }
      {
        assertion = lib.hasPrefix "/" cfg.statusPath;
        message = "the zot status marker path must be absolute";
      }
    ];

    users.groups.zot = { };
    users.users.zot = {
      isSystemUser = true;
      group = "zot";
    };

    systemd.services.zot = {
      description = "zot OCI registry";
      wantedBy = [ "multi-user.target" ];
      after = [
        "network.target"
        "tank-registry.mount"
      ];
      requires = [ "tank-registry.mount" ];
      unitConfig = {
        ConditionPathIsMountPoint = cfg.storagePath;
        RequiresMountsFor = cfg.storagePath;
      };
      serviceConfig = {
        Type = "simple";
        User = "zot";
        Group = "zot";
        RuntimeDirectory = "zot";
        RuntimeDirectoryMode = "0700";
        LoadCredential = [
          "htpasswd:${cfg.htpasswdFile}"
          "access-control.json:${cfg.accessControlFile}"
        ];
        ExecStartPre = [
          "+${ensureStoragePermissions}"
          prepareConfig
          "${cfg.package}/bin/zot verify /run/zot/config.json"
        ];
        ExecStart = "${cfg.package}/bin/zot serve /run/zot/config.json";
        Restart = "on-failure";
        RestartSec = "5s";
        NoNewPrivileges = true;
        PrivateDevices = true;
        PrivateTmp = true;
        ProtectControlGroups = true;
        ProtectHome = true;
        ProtectKernelModules = true;
        ProtectKernelTunables = true;
        ProtectSystem = "strict";
        ReadWritePaths = [ cfg.storagePath ];
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
        ];
        RestrictRealtime = true;
        SystemCallArchitectures = "native";
      };
    };

    services.nginx = {
      enable = true;
      recommendedOptimisation = true;
      recommendedProxySettings = true;
      recommendedTlsSettings = true;
      virtualHosts.${cfg.hostName} = {
        onlySSL = true;
        useACMEHost = cfg.hostName;
        locations."/" = {
          proxyPass = "http://127.0.0.1:${toString cfg.listenPort}";
          proxyWebsockets = true;
          extraConfig = ''
            client_max_body_size 0;
            proxy_request_buffering off;
            proxy_read_timeout 900s;
            proxy_send_timeout 900s;
          '';
        };
      };
    };

    services.prometheus.exporters.node = {
      enable = true;
      port = cfg.metricsPort;
      listenAddress = "0.0.0.0";
      enabledCollectors = [
        "filesystem"
        "systemd"
        "textfile"
        "zfs"
      ];
      extraFlags = [
        "--collector.systemd.unit-include=(zot|nginx|acme-${cfg.hostName}|zot-platform-metrics)\\.(service|timer)"
        "--collector.textfile.directory=/var/lib/zot-monitoring"
      ];
      openFirewall = false;
    };

    systemd.services.zot-platform-metrics = {
      description = "Publish zot platform metrics for node exporter";
      serviceConfig = {
        Type = "oneshot";
        StateDirectory = "zot-monitoring";
        StateDirectoryMode = "0755";
        ExecStart = writeMetrics;
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectHome = true;
        ProtectSystem = "strict";
      };
    };
    systemd.timers.zot-platform-metrics = {
      description = "Refresh zot platform metrics";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "5m";
        OnUnitActiveSec = "1h";
        Persistent = true;
      };
    };

    security.acme = {
      acceptTerms = true;
      defaults.email = cfg.acmeEmail;
      certs.${cfg.hostName} = {
        dnsProvider = "cloudflare";
        credentialFiles.CF_DNS_API_TOKEN_FILE = cfg.cloudflareTokenFile;
        group = "nginx";
      };
    };

    networking.firewall.extraInputRules = lib.concatStringsSep "\n" [
      (lib.concatMapStringsSep "\n" (
        network: "ip saddr ${network} tcp dport 443 accept"
      ) cfg.allowedSourceNetworks)
      (lib.concatMapStringsSep "\n" (
        network: "ip saddr ${network} tcp dport ${toString cfg.metricsPort} accept"
      ) cfg.metricsAllowedSourceNetworks)
    ];

    environment.persistence."/persist".directories = [
      "/var/lib/acme"
    ];

    systemd.tmpfiles.rules = [
      "d ${cfg.storagePath} 0750 zot zot -"
      "d /persist/zot 0750 root zot -"
      "d ${cfg.statusPath} 0750 zot zot -"
    ];
  };
}
