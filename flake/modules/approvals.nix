{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.approvals;
  python = pkgs.python313.withPackages (p: [ p.webauthn ]);
  source = pkgs.runCommand "approvals-source" { } ''
    mkdir -p $out/scripts/approvals
    cp ${../../scripts/approvals/core.py} $out/scripts/approvals/core.py
    cp ${../../scripts/approvals/server.py} $out/scripts/approvals/server.py
    cp ${../../scripts/approvals/page.html} $out/scripts/approvals/page.html
    touch $out/scripts/__init__.py $out/scripts/approvals/__init__.py
  '';
  common = "--state ${cfg.stateDir} --public-url ${cfg.publicUrl}";
  enroll = pkgs.writeShellScriptBin "approvals-enroll" ''
    exec ${pkgs.util-linux}/bin/runuser -u approvals -- \
      env PYTHONPATH=${source} ${python}/bin/python -m scripts.approvals.server ${common} enroll
  '';
in
{
  options.homelab.approvals = {
    enable = lib.mkEnableOption "approvals dashboard for held CI operations";
    port = lib.mkOption {
      type = lib.types.port;
      default = 8020;
    };
    publicUrl = lib.mkOption {
      type = lib.types.str;
      default = "https://approvals.rupan.dev";
      description = "Origin the page is served on; it is also the WebAuthn relying party.";
    };
    stateDir = lib.mkOption {
      type = lib.types.str;
      default = "/persist/approvals";
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.approvals = {
      isSystemUser = true;
      group = "approvals";
      home = cfg.stateDir;
    };
    users.groups.approvals = { };
    systemd.tmpfiles.rules = [
      "d ${cfg.stateDir} 0700 approvals approvals -"
      "z /persist/secrets/approvals-operator-token 0600 root root -"
    ];
    environment.systemPackages = [ enroll ];
    systemd.services.approvals = {
      description = "Approvals dashboard for held CI operations";
      wantedBy = [ "multi-user.target" ];
      after = [
        "network-online.target"
        "woodpecker-server.service"
      ];
      wants = [ "network-online.target" ];
      environment.PYTHONPATH = toString source;
      serviceConfig = {
        ExecStart = "${python}/bin/python -m scripts.approvals.server ${common} serve --catalog ${../../config/ci/operations.json} --listen 0.0.0.0:${toString cfg.port} --operator-token %d/operator-token";
        User = "approvals";
        Group = "approvals";
        # The operator token never leaves root-owned storage; systemd hands
        # the service a private copy for its lifetime only.
        LoadCredential = "operator-token:/persist/secrets/approvals-operator-token";
        EnvironmentFile = "-/persist/secrets/approvals.env";
        ReadWritePaths = [ cfg.stateDir ];
        Restart = "on-failure";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
      };
    };
    networking.firewall.allowedTCPPorts = [ cfg.port ];
  };
}
