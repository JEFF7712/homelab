{
  config,
  lib,
  pkgs,
  fleetInventory,
  ...
}:
let
  cfg = config.homelab.kubernetesCiCredentials;
  issuerSource = pkgs.writeTextDir "scripts/ci/kube_credentials.py" (
    builtins.readFile ../../scripts/ci/kube_credentials.py
  );
  profiles = {
    ha = {
      user = "kube-ci-ha";
      public = ../../config/ci/ha-issuer.pub;
    };
    registry-advance = {
      user = "kube-ci-registry";
      public = ../../config/ci/registry-issuer.pub;
    };
  };
  issuers = lib.mapAttrs (
    profile: identity:
    let
      issue = pkgs.writeShellScript "issue-${profile}-kubeconfig" ''
        export PATH=${lib.makeBinPath [ pkgs.kubectl ]}
        exec ${pkgs.python3}/bin/python ${issuerSource}/scripts/ci/kube_credentials.py issue \
          --profile ${profile} --server https://${fleetInventory.hosts.homelab-01.address}:6443
      '';
      forced = pkgs.writeShellScript "restricted-${profile}-kubeconfig" ''
        test -z "''${SSH_ORIGINAL_COMMAND:-}" || exit 64
        exec ${pkgs.sudo}/bin/sudo -n ${issue}
      '';
    in
    identity // { inherit issue forced; }
  ) profiles;
in
{
  options.homelab.kubernetesCiCredentials = {
    issuer = lib.mkEnableOption "fixed-profile CI TokenRequest issuers on an API server";
    client = lib.mkEnableOption "encrypted restricted issuer identities on the CI host";
  };

  config = lib.mkMerge [
    (lib.mkIf cfg.issuer {
      assertions = [
        {
          assertion = config.networking.hostName == "homelab-01";
          message = "CI credential issuers belong only on homelab-01.";
        }
      ];
      users.groups = lib.mapAttrs' (_: identity: lib.nameValuePair identity.user { }) issuers;
      users.users = lib.mapAttrs' (
        _: identity:
        lib.nameValuePair identity.user {
          isSystemUser = true;
          group = identity.user;
          shell = pkgs.bash;
          openssh.authorizedKeys.keys = [
            ''restrict,from="${fleetInventory.hosts.homelab-04.address}",command="${identity.forced}" ${lib.removeSuffix "\n" (builtins.readFile identity.public)}''
          ];
        }
      ) issuers;
      services.openssh.extraConfig = lib.concatStringsSep "\n" (
        lib.mapAttrsToList (_: identity: ''
          Match User ${identity.user}
            AllowTcpForwarding no
            AllowStreamLocalForwarding no
            PermitTTY no
            X11Forwarding no
            ForceCommand ${identity.forced}
          Match all
        '') issuers
      );
      security.sudo.extraRules = lib.mapAttrsToList (_: identity: {
        users = [ identity.user ];
        runAs = "root";
        commands = [
          {
            command = ''${identity.issue} ""'';
            options = [ "NOPASSWD" ];
          }
        ];
      }) issuers;
    })
    (lib.mkIf cfg.client {
      assertions = [
        {
          assertion = config.networking.hostName == "homelab-04";
          message = "Restricted issuer private identities belong only on the CI host.";
        }
      ];
      sops.secrets.ha-ci-issuer-key = {
        sopsFile = ../../secrets/ha-ci-issuer.sops.json;
        format = "json";
        key = "private_key";
        mode = "0400";
      };
      sops.secrets.registry-ci-issuer-key = {
        sopsFile = ../../secrets/registry-ci-issuer.sops.json;
        format = "json";
        key = "private_key";
        mode = "0400";
      };
    })
  ];
}
