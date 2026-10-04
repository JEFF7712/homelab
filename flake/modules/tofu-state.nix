{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.tofuState;
  source = pkgs.runCommand "tofu-state-source" { } ''
    mkdir -p $out/scripts/ci
    cp ${../../scripts/ci/state.py} $out/scripts/ci/state.py
    cp ${../../scripts/ci/authority.py} $out/scripts/ci/authority.py
    touch $out/scripts/__init__.py $out/scripts/ci/__init__.py
  '';
in
{
  options.homelab.tofuState.enable = lib.mkEnableOption "TLS OpenTofu state and immutable CI artifacts";
  config = lib.mkIf cfg.enable {
    environment.systemPackages = [
      (pkgs.writeShellScriptBin "ci-authority" ''
        export PYTHONPATH=${source}
        exec ${pkgs.python313}/bin/python -m scripts.ci.authority --database /persist/tofu-state/state.sqlite "$@"
      '')
    ];
    users.users.tofu-state = {
      isSystemUser = true;
      group = "tofu-state";
    };
    users.groups.tofu-state = { };
    systemd.tmpfiles.rules = [ "d /persist/tofu-state 0700 tofu-state tofu-state -" ];
    networking.firewall.allowedTCPPorts = [ 3902 ];
    systemd.services.tofu-state = {
      wantedBy = [ "multi-user.target" ];
      environment.PYTHONPATH = toString source;
      serviceConfig = {
        ExecStart = "${pkgs.python313}/bin/python -m scripts.ci.state --database /persist/tofu-state/state.sqlite --credentials /persist/tofu-state/credentials.json --certificate /persist/tofu-state/server.pem --key /persist/tofu-state/server-key.pem";
        User = "tofu-state";
        Group = "tofu-state";
        UMask = "0077";
        Restart = "on-failure";
        ReadWritePaths = [ "/persist/tofu-state" ];
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
      };
    };
  };
}
