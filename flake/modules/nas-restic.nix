# Restic REST server for workstation backups (the operator laptop's
# preserved system state and home configuration). Repositories are private
# per htpasswd user and encrypted client-side; tank/backups' daily sanoid
# snapshots keep 30 days of history even if a client prunes or overwrites
# its own repository.
{
  config,
  lib,
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
    };

    systemd.services.restic-rest-server.unitConfig.RequiresMountsFor = [ cfg.dataDir ];

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
