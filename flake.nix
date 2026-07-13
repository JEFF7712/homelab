{
  description = "Homelab devShells + CI image source";

  nixConfig = {
    extra-substituters = [ "http://10.0.20.190:8080/homelab" ];
    extra-trusted-public-keys = [ "homelab:s17u8G3szjlQ6UmMAPsszVS/J1jaw6gDwSDM9+/QeNQ=" ];
  };

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; config.allowUnfree = true; };

        ciPackages = with pkgs; [
          yamllint
          (python3.withPackages (p: [ p.pyyaml p.ansible-core ]))
          ansible-lint
          terraform
          terragrunt
          kubeconform
          kubernetes-helm
          gitleaks
          trivy
          attic-client
          git
          coreutils
          gnused
          gawk
          bash
        ];

        laptopExtras = with pkgs; [
          kubectl
          talosctl
          cilium-cli
          argocd
          k9s
          kubeseal
        ];
      in
      {
        devShells.ci = pkgs.mkShell {
          packages = ciPackages;
        };

        devShells.default = pkgs.mkShell {
          packages = ciPackages ++ laptopExtras;
          shellHook = ''echo "Welcome to the homelab dev shell."'';
        };
      });
}
