# Restic REST server for workstation backups (the operator laptop's
# preserved system state and home configuration). Repositories are private
# per htpasswd user and encrypted client-side; tank/backups' daily sanoid
# snapshots keep 30 days of history even if a client prunes or overwrites
# its own repository, and adguard-netbird-01 pulls them off-host nightly.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.resticServer;
  port = 8010;
in
{
  options.homelab.resticServer = {
    enable = lib.mkEnableOption "the restic REST server for workstation backups";
    hostName = lib.mkOption {
      type = lib.types.str;
      default = "restic.rupan.dev";
      description = "Private DNS name served by the TLS reverse proxy.";
    };
    dataDir = lib.mkOption {
      type = lib.types.path;
      default = "/tank/backups/restic";
      description = "Repository root, on the snapshotted tank/backups dataset.";
    };
    acmeEmail = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "ACME account email for the front's certificate.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.acmeEmail != null;
        message = "homelab.resticServer.acmeEmail must be set";
      }
    ];

    sops.secrets.restic-server-htpasswd = {
      owner = "restic";
      restartUnits = [ "restic-rest-server.service" ];
    };
    sops.secrets.cloudflare-dns-api-token = { };

    services.restic.server = {
      enable = true;
      inherit (cfg) dataDir;
      listenAddress = "127.0.0.1:${toString port}";
      privateRepos = true;
      htpasswd-file = config.sops.secrets.restic-server-htpasswd.path;
      # adguard-netbird-01 pulls the repositories off-host as backup-reader,
      # a member of the restic group (nas-backup-source.nix).
      extraFlags = [ "--group-accessible-repos" ];
    };

    systemd.services.restic-rest-server = {
      unitConfig.RequiresMountsFor = [ cfg.dataDir ];
      # Files written before --group-accessible-repos are owner-only.
      serviceConfig.ExecStartPre = [
        "+${pkgs.writeShellScript "restic-repos-group-readable" ''
          ${pkgs.coreutils}/bin/chmod 0750 ${cfg.dataDir}
          ${pkgs.findutils}/bin/find ${cfg.dataDir} -mindepth 1 -maxdepth 1 \
            ! -name .htpasswd -exec ${pkgs.coreutils}/bin/chmod -R g+rX {} +
        ''}"
      ];
    };

    services.nginx = {
      enable = true;
      recommendedProxySettings = true;
      recommendedTlsSettings = true;
      virtualHosts.${cfg.hostName} = {
        onlySSL = true;
        useACMEHost = cfg.hostName;
        locations."/" = {
          proxyPass = "http://127.0.0.1:${toString port}";
          extraConfig = ''
            client_max_body_size 0;
            proxy_request_buffering off;
            proxy_buffering off;
          '';
        };
      };
    };

    security.acme = {
      acceptTerms = true;
      defaults.email = cfg.acmeEmail;
      certs.${cfg.hostName} = {
        dnsProvider = "cloudflare";
        # AdGuard rewrites *.rupan.dev locally, so propagation is checked
        # against a public resolver.
        dnsResolver = "1.1.1.1:53";
        credentialFiles.CF_DNS_API_TOKEN_FILE = config.sops.secrets.cloudflare-dns-api-token.path;
        group = "nginx";
      };
    };

    networking.firewall.allowedTCPPorts = [ 443 ];
  };
}
