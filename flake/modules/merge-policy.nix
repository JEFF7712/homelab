{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.mergePolicy;
  python = pkgs.python313.withPackages (p: [ p.pyyaml ]);
  # The policy runs from this generation's copy, never from a PR checkout,
  # so a change to the verifier takes effect through the validated CI-host
  # deployment, with task authorization for enrolled repository authors.
  source = pkgs.runCommand "merge-policy-source" { } ''
    mkdir -p $out/scripts/verifier
    cp ${../../scripts/verifier/websites.py} $out/scripts/verifier/websites.py
    cp ${../../scripts/verifier/service.py} $out/scripts/verifier/service.py
    cp ${../../scripts/verifier/freshness.py} $out/scripts/verifier/freshness.py
    touch $out/scripts/__init__.py $out/scripts/verifier/__init__.py
  '';
in
{
  options.homelab.mergePolicy = {
    enable = lib.mkEnableOption "per-commit merge policy status for homelab PRs";
    forgejo = lib.mkOption {
      type = lib.types.str;
      default = "http://git.internal:3000";
    };
    repository = lib.mkOption {
      type = lib.types.str;
      default = "JEFF7712/homelab";
    };
    interval = lib.mkOption {
      type = lib.types.int;
      default = 15;
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.merge-policy = {
      isSystemUser = true;
      group = "merge-policy";
    };
    users.groups.merge-policy = { };
    sops.secrets.merge-policy-token.restartUnits = [ "merge-policy.service" ];
    systemd.services.merge-policy = {
      description = "Per-commit merge policy status for homelab PRs";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      path = [ pkgs.git ];
      environment.PYTHONPATH = toString source;
      serviceConfig = {
        ExecStart = "${python}/bin/python -m scripts.verifier.service --forgejo ${cfg.forgejo} --repository ${cfg.repository} --token-file %d/token --state %S/merge-policy --interval ${toString cfg.interval} --autonomous";
        User = "merge-policy";
        Group = "merge-policy";
        StateDirectory = "merge-policy";
        StateDirectoryMode = "0700";
        # The homelab-policy token stays in root-owned storage; systemd hands
        # the service a private copy for its lifetime only.
        LoadCredential = "token:${config.sops.secrets.merge-policy-token.path}";
        Restart = "always";
        RestartSec = 10;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
        ];
        SystemCallFilter = [ "@system-service" ];
        MemoryMax = "512M";
      };
    };
  };
}
