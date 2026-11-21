# Read-only TLS front for the niks3 cache, so substituters name a host
# instead of nas-01's address. Substituters only read, so it admits GET and
# HEAD; pushes keep using the plain port with the API token.
{ config, lib, ... }:
let
  cache = config.homelab.niks3Cache;
  cfg = cache.tls;
in
{
  options.homelab.niks3Cache.tls = {
    enable = lib.mkEnableOption "a read-only TLS front for the niks3 cache";
    hostName = lib.mkOption {
      type = lib.types.str;
      default = "nix.rupan.dev";
      description = "Private DNS name served by the TLS front.";
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
        assertion = cache.enable;
        message = "homelab.niks3Cache.tls requires homelab.niks3Cache";
      }
      {
        assertion = cfg.acmeEmail != null;
        message = "homelab.niks3Cache.tls.acmeEmail must be set";
      }
    ];

    sops.secrets.cloudflare-dns-api-token = { };

    services.nginx = {
      enable = true;
      recommendedProxySettings = true;
      recommendedTlsSettings = true;
      virtualHosts.${cfg.hostName} = {
        onlySSL = true;
        useACMEHost = cfg.hostName;
        locations."/" = {
          proxyPass = "http://127.0.0.1:${toString cache.port}";
          extraConfig = ''
            limit_except GET {
              deny all;
            }
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
