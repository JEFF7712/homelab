{ config, lib, ... }:
let
  credentials = [
    "forgejo-read"
    "forgejo-write"
    "woodpecker-read"
    "woodpecker-write"
    "registry-password"
  ];
in
{
  imports = [ ../modules/projectctl.nix ];
  sops = {
    age.sshKeyPaths = [ "/persist/etc/ssh/ssh_host_ed25519_key" ];
    gnupg.sshKeyPaths = [ ];
    secrets = lib.genAttrs credentials (name: {
      sopsFile = ../../secrets/projectctl.sops.json;
      format = "json";
      key = name;
      mode = "0400";
      owner = if lib.hasSuffix "-read" name then "projectctl-worker" else "root";
      group = if lib.hasSuffix "-read" name then "projectctl-worker" else "root";
    });
  };
  homelab.projectctl = {
    enable = true;
    principals = builtins.fromJSON (builtins.readFile ../../config/projectctl/callers.json);
    cloneIdentity = "homelab-ci-source";
    registryReadIdentity = "projectctl-pull";
    endpoints = {
      forgejo = {
        url = "https://git.rupan.dev";
        allow_plaintext = false;
      };
      woodpecker = {
        url = "http://127.0.0.1:8000";
        allow_plaintext = false;
      };
    };
    credentialFiles = lib.genAttrs credentials (name: config.sops.secrets.${name}.path);
  };
}
