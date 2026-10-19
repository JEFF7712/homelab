{ ... }:
{
  imports = [ ../modules/projectctl.nix ];
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
    credentialFiles = {
      forgejo-read = "/persist/projectctl-credentials/forgejo-read";
      forgejo-write = "/persist/projectctl-credentials/forgejo-write";
      woodpecker-read = "/persist/projectctl-credentials/woodpecker-read";
      woodpecker-write = "/persist/projectctl-credentials/woodpecker-write";
      registry-password = "/persist/projectctl-credentials/registry-password";
    };
  };
}
