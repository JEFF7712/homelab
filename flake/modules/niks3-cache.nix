{
  config,
  lib,
  options,
  pkgs,
  ...
}:
let
  cfg = config.homelab.niks3Cache;
  secretsDir = "/persist/niks3";
  garageUser = "garage-nix-cache";
  garageConfig = (pkgs.formats.toml { }).generate "garage-nix-cache.toml" {
    metadata_dir = "${cfg.storageDir}/meta";
    data_dir = "${cfg.storageDir}/data";
    db_engine = "sqlite";
    replication_factor = 1;
    rpc_bind_addr = "127.0.0.1:${toString cfg.garageRpcPort}";
    rpc_public_addr = "127.0.0.1:${toString cfg.garageRpcPort}";
    rpc_secret_file = "${cfg.storageDir}/rpc-secret";
    s3_api = {
      api_bind_addr = "127.0.0.1:${toString cfg.garageS3Port}";
      s3_region = "homelab";
    };
  };
  garage = "${lib.getExe pkgs.garage} -c ${garageConfig}";
in
{
  options.homelab.niks3Cache = {
    enable = lib.mkEnableOption "niks3 Nix binary cache with its own Garage store";
    port = lib.mkOption {
      type = lib.types.port;
      default = 8090;
      description = "Port serving the cache and the niks3 push API.";
    };
    storageDir = lib.mkOption {
      type = lib.types.path;
      default = "/srv/nix-cache";
      description = "Garage metadata and data directory; must sit on SSD.";
    };
    capacity = lib.mkOption {
      type = lib.types.str;
      default = "75G";
      description = "Garage layout capacity, kept below the dataset quota.";
    };
    garageS3Port = lib.mkOption {
      type = lib.types.port;
      default = 3910;
      description = "Loopback S3 port of the cache's Garage.";
    };
    garageRpcPort = lib.mkOption {
      type = lib.types.port;
      default = 3911;
      description = "Loopback RPC port of the cache's Garage.";
    };
    bucket = lib.mkOption {
      type = lib.types.str;
      default = "homelab-nix-cache";
      description = "Garage bucket holding narinfos and NARs.";
    };
    signingKeyName = lib.mkOption {
      type = lib.types.str;
      default = "homelab-niks3-1";
      description = "Name of the narinfo signing key generated on first start.";
    };
  };

  config = lib.mkIf cfg.enable {
    networking.firewall.allowedTCPPorts = [ cfg.port ];

    environment.persistence."/persist".directories = [
      {
        directory = "/var/lib/postgresql";
        user = "postgres";
        group = "postgres";
        mode = "0750";
      }
    ];

    users.users.${garageUser} = {
      isSystemUser = true;
      group = garageUser;
    };
    users.groups.${garageUser} = { };

    # The fleet's main Garage keeps data on the tank HDD, where many small
    # reads stalled CI (see the Attic move in docs/runbooks/ci-pipelines.md),
    # so the cache gets its own instance on the SSD pool.
    systemd.services.garage-nix-cache = {
      description = "Garage S3 store for the niks3 cache";
      after = [ "network.target" ];
      wantedBy = [ "multi-user.target" ];
      unitConfig.RequiresMountsFor = [ cfg.storageDir ];
      serviceConfig = {
        ExecStartPre = [
          "+${pkgs.writeShellScript "prepare-garage-nix-cache" ''
            set -eu
            ${pkgs.coreutils}/bin/install -d -m 0750 -o niks3 -g niks3 ${secretsDir}
            ${pkgs.coreutils}/bin/install -d -m 0700 -o ${garageUser} -g ${garageUser} \
              ${cfg.storageDir}/meta ${cfg.storageDir}/data
            if [ ! -s ${cfg.storageDir}/rpc-secret ]; then
              (umask 077; ${pkgs.coreutils}/bin/od -A n -v -t x1 -N 32 /dev/urandom \
                | ${pkgs.coreutils}/bin/tr -d ' \n' > ${cfg.storageDir}/rpc-secret)
            fi
            ${pkgs.coreutils}/bin/chown ${garageUser}:${garageUser} ${cfg.storageDir}/rpc-secret
            ${pkgs.coreutils}/bin/chmod 0600 ${cfg.storageDir}/rpc-secret
          ''}"
        ];
        ExecStart = "${garage} server";
        User = garageUser;
        Group = garageUser;
        Restart = "always";
        RestartSec = "5s";
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ReadWritePaths = [ cfg.storageDir ];
      };
    };

    services.niks3 = {
      enable = true;
      # Upstream v1.16.0 can leave a narinfo pointing at a NAR Garage dropped
      # when parallel pushes share it (Mic92/niks3#661).
      serverPackage = options.services.niks3.serverPackage.default.overrideAttrs (old: {
        patches = (old.patches or [ ]) ++ [ ../patches/niks3-garage-multipart-aborts.patch ];
      });
      httpAddr = "0.0.0.0:${toString cfg.port}";
      database.createLocally = true;
      s3 = {
        endpoint = "127.0.0.1:${toString cfg.garageS3Port}";
        inherit (cfg) bucket;
        region = "homelab";
        useSSL = false;
        bucketLookup = "path";
        accessKeyFile = "${secretsDir}/s3-access-key";
        secretKeyFile = "${secretsDir}/s3-secret-key";
      };
      apiTokenFile = "${secretsDir}/api-token";
      signKeyFiles = [ "${secretsDir}/signing-key" ];
      readProxy.enable = true;
    };

    # Assigns the Garage layout, then creates the bucket, its key and the
    # niks3 secrets on first start; existing ones are left alone.
    systemd.services.niks3-provision = {
      description = "Provision the niks3 Garage layout, bucket, credentials and signing key";
      after = [ "garage-nix-cache.service" ];
      requires = [ "garage-nix-cache.service" ];
      before = [ "niks3.service" ];
      requiredBy = [ "niks3.service" ];
      path = [
        pkgs.coreutils
        pkgs.gawk
        pkgs.gnugrep
        config.nix.package
      ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        UMask = "0077";
      };
      script = ''
        set -euo pipefail

        for _ in $(seq 30); do
          ${garage} status >/dev/null 2>&1 && break
          sleep 1
        done
        if ${garage} status | grep -q 'NO ROLE'; then
          node=$(${garage} node id -q | cut -d@ -f1)
          version=$(${garage} layout show | awk '/Current cluster layout version:/ {print $NF}')
          ${garage} layout assign -z homelab -c ${cfg.capacity} "$node"
          ${garage} layout apply --version $((version + 1))
        fi

        ${garage} bucket info ${cfg.bucket} >/dev/null 2>&1 || ${garage} bucket create ${cfg.bucket}
        ${garage} key info niks3 >/dev/null 2>&1 || ${garage} key create niks3
        ${garage} bucket allow --read --write ${cfg.bucket} --key niks3 >/dev/null

        key_info=$(${garage} key info --show-secret niks3)
        key_id=$(awk -F': *' '/^Key ID:/ {print $2}' <<<"$key_info")
        secret_key=$(awk -F': *' '/^Secret key:/ {print $2}' <<<"$key_info")
        if [ -z "$key_id" ] || [ -z "$secret_key" ]; then
          echo "could not read the niks3 key from garage-nix-cache" >&2
          exit 1
        fi
        printf '%s' "$key_id" > ${secretsDir}/s3-access-key
        printf '%s' "$secret_key" > ${secretsDir}/s3-secret-key

        if [ ! -s ${secretsDir}/api-token ]; then
          od -A n -v -t x1 -N 32 /dev/urandom | tr -d ' \n' > ${secretsDir}/api-token
        fi
        if [ ! -s ${secretsDir}/signing-key ]; then
          nix --extra-experimental-features nix-command key generate-secret \
            --key-name ${cfg.signingKeyName} > ${secretsDir}/signing-key
        fi
        nix --extra-experimental-features nix-command key convert-secret-to-public \
          < ${secretsDir}/signing-key > ${secretsDir}/signing-key.pub

        chown niks3:niks3 ${secretsDir}/s3-access-key ${secretsDir}/s3-secret-key \
          ${secretsDir}/api-token ${secretsDir}/signing-key
        chmod 0400 ${secretsDir}/s3-access-key ${secretsDir}/s3-secret-key \
          ${secretsDir}/api-token ${secretsDir}/signing-key
        chmod 0444 ${secretsDir}/signing-key.pub
      '';
    };
  };
}
