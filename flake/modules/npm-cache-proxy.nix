{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.homelab-npm-cache;
in
{
  options.services.homelab-npm-cache = {
    enable = lib.mkEnableOption "the pull-through npm registry cache";

    hostName = lib.mkOption {
      type = lib.types.str;
      default = "npm.rupan.dev";
      description = "Private DNS name served by the TLS reverse proxy.";
    };

    cachePath = lib.mkOption {
      type = lib.types.str;
      default = "/tank/npm-cache";
      description = "Mounted local filesystem holding the nginx proxy cache. Regenerable from upstream; never backed up.";
    };

    upstream = lib.mkOption {
      type = lib.types.str;
      default = "https://registry.npmjs.org";
      description = "Upstream npm registry proxied on cache miss.";
    };

    cloudflareTokenFile = lib.mkOption {
      type = lib.types.str;
      default = "/persist/zot/cloudflare-dns-api-token";
      description = "Cloudflare token for ACME DNS-01. Shared with the registry issuance: same zone-scoped token, no new secret to provision.";
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
      description = "IPv4 networks permitted to connect to the npm cache TLS endpoint. Port 443 is already accepted for these networks by the zot module.";
    };

    maxCacheSize = lib.mkOption {
      type = lib.types.str;
      default = "100g";
      description = "Upper bound for the proxy cache on disk.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.acmeEmail != null;
        message = "services.homelab-npm-cache.acmeEmail must be set";
      }
      {
        assertion = cfg.cachePath == "/tank/npm-cache";
        message = "homelab-npm-cache storage must use the tank/npm-cache dataset at /tank/npm-cache";
      }
      {
        assertion = lib.hasPrefix "/" cfg.cloudflareTokenFile;
        message = "the npm cache Cloudflare token path must be absolute";
      }
    ];

    # The host serves npm.rupan.dev itself, so resolve it locally.
    networking.hosts = {
      "10.0.30.20" = [ cfg.hostName ];
    };

    services.nginx = {
      enable = true;
      recommendedOptimisation = true;
      recommendedProxySettings = true;
      recommendedTlsSettings = true;
      appendHttpConfig = ''
        proxy_cache_path ${cfg.cachePath} levels=1:2 keys_zone=npm:128m max_size=${cfg.maxCacheSize} inactive=60d use_temp_path=off;
      '';
      virtualHosts.${cfg.hostName} = {
        onlySSL = true;
        useACMEHost = cfg.hostName;
          locations."/" = {
          proxyPass = cfg.upstream;
          extraConfig = ''
            proxy_ssl_server_name on;
            proxy_ssl_verify on;
            proxy_ssl_trusted_certificate ${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt;
            proxy_set_header Host registry.npmjs.org;

            # Pull-through cache: immutable tarballs cache long, packuments
            # revalidate on upstream Cache-Control. Authenticated requests
            # (private scopes, login) always bypass and are never stored.
            proxy_cache npm;
            proxy_cache_methods GET HEAD;
            proxy_cache_bypass $http_authorization;
            proxy_no_cache $http_authorization;
            proxy_cache_valid 200 10m;
            proxy_cache_lock on;
            proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
          '';
        };
          locations."~ \\.tgz$" = {
          proxyPass = cfg.upstream;
          extraConfig = ''
            proxy_ssl_server_name on;
            proxy_ssl_verify on;
            proxy_ssl_trusted_certificate ${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt;
            proxy_set_header Host registry.npmjs.org;
            proxy_cache npm;
            proxy_cache_valid 200 365d;
            proxy_cache_lock on;
            proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
          '';
        };
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

    # /var/lib/acme persistence is provided by the zot module on nas-01;
    # declaring it here too fails the duplicate-persistence assertion.

    systemd.tmpfiles.rules = [
      "d ${cfg.cachePath} 0750 nginx nginx -"
    ];
  };
}
