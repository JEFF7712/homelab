{
  description = "Homelab devShells + CI image source";

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
          gitleaks
          trivy
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
          kubernetes-helm
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
