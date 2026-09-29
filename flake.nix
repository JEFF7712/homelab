{
  description = "homelab devShells";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };

        ciPackages = with pkgs; [
          age
          apache2-utils
          git
          gitleaks
          kubeconform
          kubectl
          kubernetes-helm
          nixfmt
          opentofu
          pyright
          python313
          ruff
          sops
          yamllint
        ];

        laptopExtras = with pkgs; [
          deploy-rs
          fluxcd
          k9s
          nixos-anywhere
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
      }
    );
}
