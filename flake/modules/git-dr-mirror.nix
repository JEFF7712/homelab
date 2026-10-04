{
  config,
  lib,
  pkgs,
  ...
}:
let
  source = pkgs.runCommand "git-dr-mirror-source" { } ''
    mkdir -p $out/scripts/ci
    cp ${../../scripts/ci/dr_mirror.py} $out/scripts/ci/dr_mirror.py
    touch $out/scripts/__init__.py $out/scripts/ci/__init__.py
  '';
in
{
  config = lib.mkIf config.homelab.forgejo.enable {
    systemd.services.git-dr-mirror = {
      path = [
        pkgs.git
        pkgs.bash
      ];
      environment.PYTHONPATH = toString source;
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${pkgs.python313}/bin/python -m scripts.ci.dr_mirror";
        EnvironmentFile = "/persist/forgejo/dr-mirror.env";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
      };
    };
    systemd.timers.git-dr-mirror = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "5m";
        OnUnitActiveSec = "5m";
        Persistent = true;
      };
    };
  };
}
