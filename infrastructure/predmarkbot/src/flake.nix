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
            # libstdc++ is needed at runtime by numpy/scipy/pandas wheels
            # pulled in by the `research` dep-group.
            pkgs.stdenv.cc.cc.lib
            pkgs.zlib
          ];
          shellHook = ''
            export UV_PYTHON=${pkgs.python312}/bin/python3.12
            export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib:${pkgs.zlib}/lib:$LD_LIBRARY_PATH"
            echo "predmarkbot dev shell — python $(python3 --version), uv $(uv --version)"
          '';
        };
      });
}
