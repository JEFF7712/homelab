{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.forgejo;
in
{
  options.homelab.forgejo = {
    enable = lib.mkEnableOption "Forgejo Git service on nas-01";
    hostName = lib.mkOption {
      type = lib.types.str;
      default = "git.rupan.dev";
      description = "Public domain name for Forgejo HTTPS interface.";
    };
    httpPort = lib.mkOption {
      type = lib.types.port;
      default = 3000;
      description = "HTTP listen port for Forgejo web and API.";
    };
    sshPort = lib.mkOption {
      type = lib.types.port;
      default = 2222;
      description = "SSH listen port for Forgejo built-in SSH server.";
    };
    acmeEmail = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Contact email for Forgejo ACME TLS certificate.";
    };
    cloudflareTokenFile = lib.mkOption {
      type = lib.types.str;
      default = "/persist/zot/cloudflare-dns-api-token";
      description = "Path to Cloudflare DNS API token file for DNS-01 validation.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.acmeEmail != null;
        message = "homelab.forgejo.acmeEmail must be set when Forgejo is enabled";
      }
    ];

    networking.firewall.allowedTCPPorts = [
      cfg.httpPort
      cfg.sshPort
    ];

    networking.hosts = {
      "10.0.30.20" = [ cfg.hostName ];
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
          proxyPass = "http://127.0.0.1:${toString cfg.httpPort}";
          proxyWebsockets = true;
          extraConfig = ''
            client_max_body_size 512M;
          '';
        };
      };
    };

    security.acme = lib.mkIf (cfg.acmeEmail != null) {
      acceptTerms = true;
      defaults.email = lib.mkDefault cfg.acmeEmail;
      certs.${cfg.hostName} = {
        email = cfg.acmeEmail;
        dnsProvider = "cloudflare";
        credentialFiles.CF_DNS_API_TOKEN_FILE = cfg.cloudflareTokenFile;
        group = "nginx";
      };
    };

    systemd.tmpfiles.rules = [
      "d /persist/forgejo 0750 forgejo forgejo -"
      "d /persist/forgejo/data 0750 forgejo forgejo -"
      "d /tank/forgejo 0750 forgejo forgejo -"
      "d /tank/forgejo/repositories 0750 forgejo forgejo -"
    ];

    services.forgejo = {
      enable = true;
      database = {
        type = "sqlite3";
        path = "/persist/forgejo/data/forgejo.db";
      };
      stateDir = "/persist/forgejo";
      repositoryRoot = "/tank/forgejo/repositories";
      settings = {
        DEFAULT = {
          APP_NAME = "Homelab Git";
        };
        server = {
          DOMAIN = cfg.hostName;
          ROOT_URL = "https://${cfg.hostName}/";
          HTTP_ADDR = "0.0.0.0";
          HTTP_PORT = cfg.httpPort;
          SSH_PORT = cfg.sshPort;
          SSH_LISTEN_PORT = cfg.sshPort;
          START_SSH_SERVER = true;
        };
        service = {
          DISABLE_REGISTRATION = true;
          REQUIRE_SIGNIN_VIEW = true;
        };
        mirror = {
          ENABLED = false;
          MIN_INTERVAL = "5m";
          DEFAULT_INTERVAL = "30m";
        };
        webhook = {
          ALLOWED_HOST_LIST = "ci.internal,flux-wh-33b0c8004348.rupan.dev,*.internal,10.0.0.0/8,192.168.0.0/16";
        };
      };
    };

    systemd.services.forgejo = {
      after = [ "tank-forgejo.mount" ];
      requires = [ "tank-forgejo.mount" ];
      serviceConfig = {
        ExecStartPre = [
          "+${pkgs.writeShellScript "ensure-forgejo-storage-permissions" ''
            set -eu
            ${pkgs.coreutils}/bin/install -d -m 0750 -o forgejo -g forgejo /tank/forgejo
            ${pkgs.coreutils}/bin/install -d -m 0750 -o forgejo -g forgejo /tank/forgejo/repositories
            ${pkgs.coreutils}/bin/install -d -m 0700 -o forgejo -g forgejo /persist/forgejo
            ${pkgs.coreutils}/bin/install -d -m 0700 -o forgejo -g forgejo /persist/forgejo/data
          ''}"
        ];
      };
    };
  };
}
