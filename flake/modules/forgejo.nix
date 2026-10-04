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
  };

  config = lib.mkIf cfg.enable {
    networking.firewall.allowedTCPPorts = [
      cfg.httpPort
      cfg.sshPort
    ];

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
          DOMAIN = "git.internal";
          ROOT_URL = "http://git.internal:${toString cfg.httpPort}/";
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
