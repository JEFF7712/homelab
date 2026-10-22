{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.tofuState;
  secret = name: config.sops.secrets."tofu-state-${name}".path;
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
    sops.secrets =
      lib.genAttrs
        (map (name: "tofu-state-${name}") [
          "credentials.json"
          "server.pem"
          "server-key.pem"
        ])
        (_: {
          owner = "tofu-state";
          restartUnits = [ "tofu-state.service" ];
        });
    systemd.tmpfiles.rules = [ "d /persist/tofu-state 0700 tofu-state tofu-state -" ];
    networking.firewall.allowedTCPPorts = [ 3902 ];
    systemd.services.tofu-state = {
      wantedBy = [ "multi-user.target" ];
      environment.PYTHONPATH = toString source;
      serviceConfig = {
        ExecStart = "${pkgs.python313}/bin/python -m scripts.ci.state --database /persist/tofu-state/state.sqlite --credentials ${secret "credentials.json"} --certificate ${secret "server.pem"} --key ${secret "server-key.pem"}";
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
