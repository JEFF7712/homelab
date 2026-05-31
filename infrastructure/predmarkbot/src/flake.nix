{
  description = "predmarkbot — Kalshi prediction-market trading bot";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.flake-utils.url = "github:numtide/flake-utils";

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let pkgs = import nixpkgs { inherit system; };
      in {
        devShells.default = pkgs.mkShell {
          packages = [
            pkgs.python312
            pkgs.uv
            pkgs.sqlite
          ];
          shellHook = ''
            export UV_PYTHON=${pkgs.python312}/bin/python3.12
            echo "predmarkbot dev shell — python $(python3 --version), uv $(uv --version)"
          '';
        };
      });
}
