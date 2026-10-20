{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.projectctl;
  package = import ../packages/projectctl.nix { inherit pkgs; };
  stateDirectory = "/persist/projectctl";
  principalType = lib.types.submodule {
    options = {
      publicKey = lib.mkOption { type = lib.types.str; };
      projects = lib.mkOption {
        type = lib.types.listOf (
          lib.types.submodule {
            options = {
              project_key = lib.mkOption { type = lib.types.str; };
              repository = lib.mkOption { type = lib.types.str; };
              visibility = lib.mkOption {
                type = lib.types.enum [
                  "private"
                  "public"
                ];
                default = "private";
              };
              routine = lib.mkOption {
                default = null;
                description = "Preapproved onboarding of this existing repository, branch and sandbox CI contract.";
                type = lib.types.nullOr (
                  lib.types.submodule {
                    options = {
                      repository_id = lib.mkOption { type = lib.types.ints.positive; };
                      default_branch = lib.mkOption { type = lib.types.str; };
                      ci_contract = lib.mkOption { type = lib.types.enum [ "python-uv-v1" ]; };
                    };
                  }
                );
              };
            };
          }
        );
      };
    };
  };
  scopes = lib.concatLists (
    lib.mapAttrsToList (
      principal: value: map (project: project // { inherit principal; }) value.projects
    ) cfg.principals
  );
  contractDigest = "sha256:" + builtins.hashFile "sha256" ../../config/ci/projectctl-contracts.json;
  settings = pkgs.writeText "projectctl-config.json" (
    builtins.toJSON {
      schema_version = 1;
      platform = {
        forgejo = "homelab-forgejo";
        woodpecker = "homelab-woodpecker";
        homelab_repository = "JEFF7712/homelab";
      };
      endpoints = cfg.endpoints;
      scopes = scopes;
      trusted_keys = cfg.operatorPublicKeys;
      secret_files = cfg.credentialFiles;
      state_directory = stateDirectory;
      clone_identity = cfg.cloneIdentity;
      registry = {
        address = cfg.registryAddress;
        username = cfg.registryReadIdentity;
        actions = [ "read" ];
      };
      deployed_policy_file = "/etc/projectctl/ci-policy.json";
      expected_ci_digest = contractDigest;
    }
  );
in
{
  options.homelab.projectctl = {
    enable = lib.mkEnableOption "restricted project onboarding gateway and trusted worker";
    principals = lib.mkOption {
      type = lib.types.attrsOf principalType;
      default = { };
      description = "Explicit caller SSH keys and allowed project identities.";
    };
    operatorPublicKeys = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = builtins.fromJSON (builtins.readFile ../../config/projectctl/operators.json);
      description = "Operator key IDs mapped to base64-encoded raw Ed25519 public keys.";
    };
    credentialFiles = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      description = "Protected paths for forgejo-read, forgejo-write, woodpecker-read, woodpecker-write, and registry-password.";
    };
    endpoints = lib.mkOption {
      type = lib.types.attrsOf (
        lib.types.submodule {
          options = {
            url = lib.mkOption { type = lib.types.str; };
            allow_plaintext = lib.mkOption {
              type = lib.types.bool;
              default = false;
            };
          };
        }
      );
      default = { };
      description = "Explicit Forgejo and Woodpecker API origins; HTTPS verifies certificates.";
    };
    cloneIdentity = lib.mkOption { type = lib.types.str; };
    registryAddress = lib.mkOption {
      type = lib.types.str;
      default = "registry.rupan.dev";
    };
    registryReadIdentity = lib.mkOption { type = lib.types.str; };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion =
          lib.all (principal: builtins.match "[A-Za-z0-9][A-Za-z0-9_.-]{0,63}" principal != null) (
            builtins.attrNames cfg.principals
          )
          && lib.all (
            value: builtins.match "ssh-ed25519 [A-Za-z0-9+/=]+( [A-Za-z0-9_.@-]+)?" value.publicKey != null
          ) (builtins.attrValues cfg.principals);
        message = "projectctl caller enrollment requires safe principal names and a single Ed25519 SSH public key per caller.";
      }
      {
        assertion = cfg.principals != { } && cfg.operatorPublicKeys != { } && scopes != [ ];
        message = "projectctl requires real caller enrollment, project scopes, and an independently held operator key.";
      }
      {
        assertion =
          builtins.attrNames cfg.credentialFiles == [
            "forgejo-read"
            "forgejo-write"
            "registry-password"
            "woodpecker-read"
            "woodpecker-write"
          ];
        message = "projectctl requires all five explicitly provisioned credential file paths.";
      }
      {
        assertion =
          builtins.attrNames cfg.endpoints == [
            "forgejo"
            "woodpecker"
          ];
        message = "projectctl requires explicit Forgejo and Woodpecker API origins.";
      }
      {
        assertion = config.homelab.woodpecker.enable or false;
        message = "projectctl must run beside the immutable Woodpecker policy attestation.";
      }
    ];
    environment.systemPackages = [ package ];
    environment.etc."projectctl/config.json".source = settings;
    users.groups.projectctl-worker = { };
    users.users.projectctl-worker = {
      isSystemUser = true;
      group = "projectctl-worker";
      home = stateDirectory;
      shell = pkgs.bashInteractive;
      openssh.authorizedKeys.keys = lib.mapAttrsToList (
        principal: value:
        ''restrict,command="${package}/bin/projectctl-gateway --config ${settings} --principal ${principal}" ${value.publicKey}''
      ) cfg.principals;
    };
    systemd.tmpfiles.rules = [ "d ${stateDirectory} 0700 projectctl-worker projectctl-worker -" ];
    systemd.services."projectctl-dispatch@" = {
      description = "Authorized project onboarding request %i";
      unitConfig.RequiresMountsFor = [ stateDirectory ];
      serviceConfig = {
        Type = "oneshot";
        User = "projectctl-worker";
        Group = "projectctl-worker";
        ExecStart = "${package}/bin/projectctl-operator dispatch %i --config ${settings}";
        LoadCredential = lib.mapAttrsToList (name: path: "${name}:${path}") cfg.credentialFiles;
        UMask = "0077";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        NoNewPrivileges = true;
        RestrictSUIDSGID = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
        ];
        CapabilityBoundingSet = "";
        LockPersonality = true;
        ReadWritePaths = [ stateDirectory ];
      };
    };
    systemd.services.projectctl-verify-credentials = {
      description = "Verify dedicated projectctl service identities without mutations";
      unitConfig.RequiresMountsFor = [ stateDirectory ];
      serviceConfig = config.systemd.services."projectctl-dispatch@".serviceConfig // {
        ExecStart = "${package}/bin/projectctl-operator verify-credentials --config ${settings}";
      };
    };
    systemd.services.projectctl-reconcile = {
      description = "Reconcile preapproved project onboarding requests";
      unitConfig.RequiresMountsFor = [ stateDirectory ];
      serviceConfig = config.systemd.services."projectctl-dispatch@".serviceConfig // {
        ExecStart = "${package}/bin/projectctl-operator reconcile --config ${settings}";
      };
    };
    systemd.timers.projectctl-reconcile = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "30s";
        OnUnitInactiveSec = "30s";
        AccuracySec = "5s";
      };
    };
  };
}
