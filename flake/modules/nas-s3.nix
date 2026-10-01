{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.s3;
in
{
  options.homelab.s3 = {
    enable = lib.mkEnableOption "Self-hosted S3 service (Garage) on nas-01";
    apiPort = lib.mkOption {
      type = lib.types.port;
      default = 3900;
      description = "S3 API port.";
    };
    rpcPort = lib.mkOption {
      type = lib.types.port;
      default = 3901;
      description = "Internal RPC port.";
    };
    environmentFile = lib.mkOption {
      type = lib.types.path;
      default = "/persist/garage/garage.env";
      description = "File containing GARAGE_RPC_SECRET.";
    };
  };

  config = lib.mkIf cfg.enable {
    networking.firewall.allowedTCPPorts = [
      cfg.apiPort
    ];

    users.users.garage = {
      isSystemUser = true;
      group = "garage";
    };
    users.groups.garage = { };

    systemd.tmpfiles.rules = [
      "d /persist/garage 0700 garage garage -"
      "d /persist/garage/meta 0700 garage garage -"
      "d /tank/s3 0750 garage garage -"
    ];

    services.garage = {
      enable = true;
      package = pkgs.garage;
      environmentFile = cfg.environmentFile;
      settings = {
        metadata_dir = "/persist/garage/meta";
        data_dir = "/tank/s3";
        db_engine = "sqlite";
        replication_factor = 1;
        rpc_bind_addr = "[::]:${toString cfg.rpcPort}";
        rpc_public_addr = "127.0.0.1:${toString cfg.rpcPort}";
        s3_api = {
          api_bind_addr = "[::]:${toString cfg.apiPort}";
          s3_region = "homelab";
          root_domain = ".s3.internal";
        };
      };
    };

    systemd.services.garage = {
      after = [ "tank-s3.mount" ];
      requires = [ "tank-s3.mount" ];
      serviceConfig = {
        DynamicUser = lib.mkForce false;
        User = "garage";
        Group = "garage";
        ExecStartPre = [
          "+${pkgs.writeShellScript "ensure-garage-storage-permissions" ''
            set -eu
            ${pkgs.coreutils}/bin/install -d -m 0700 -o garage -g garage /persist/garage
            ${pkgs.coreutils}/bin/install -d -m 0700 -o garage -g garage /persist/garage/meta
            ${pkgs.coreutils}/bin/install -d -m 0750 -o garage -g garage /tank/s3
            ${pkgs.coreutils}/bin/chown -R garage:garage /persist/garage /tank/s3
            if [ ! -f ${lib.escapeShellArg cfg.environmentFile} ]; then
              secret=$(${pkgs.coreutils}/bin/od -A n -v -t x1 /dev/urandom | ${pkgs.coreutils}/bin/tr -d ' \n' | ${pkgs.coreutils}/bin/head -c 64)
              echo "GARAGE_RPC_SECRET=$secret" > ${lib.escapeShellArg cfg.environmentFile}
              ${pkgs.coreutils}/bin/chown garage:garage ${lib.escapeShellArg cfg.environmentFile}
              ${pkgs.coreutils}/bin/chmod 0600 ${lib.escapeShellArg cfg.environmentFile}
            fi
          ''}"
        ];
      };
    };
  };
}
