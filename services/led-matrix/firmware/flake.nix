{
  description = "Pico W HUB75 receiver firmware for the LED matrix service";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
      };
      picoSdk = pkgs.pico-sdk.override {
        withSubmodules = true;
      };
    in
    {
      packages.${system} = rec {
        firmware = pkgs.stdenv.mkDerivation {
          pname = "pico-w-hub75-receiver";
          version = "1.0.0";
          src = pkgs.lib.fileset.toSource {
            root = ./.;
            fileset = pkgs.lib.fileset.unions [
              ./CMakeLists.txt
              ./hub75.pio
              ./lwipopts.h
              ./main.c
              ./tusb_config.h
              ./usb_descriptors.c
            ];
          };

          nativeBuildInputs = with pkgs; [
            cmake
            ninja
            gcc-arm-embedded
            python3
            picotool
          ];

          PICO_SDK_PATH = "${picoSdk}/lib/pico-sdk";

          dontUseCmakeConfigure = true;

          buildPhase = ''
            runHook preBuild
            env -u CC -u CXX cmake -B build -G Ninja -DPICO_BOARD=pico_w -DCMAKE_BUILD_TYPE=Release
            env -u CC -u CXX cmake --build build -j$NIX_BUILD_CORES
            runHook postBuild
          '';

          installPhase = ''
            runHook preInstall
            mkdir -p $out
            cp build/pico_w_hub75_receiver.uf2 build/pico_w_hub75_receiver.elf build/pico_w_hub75_receiver.bin $out/
            runHook postInstall
          '';
        };
        default = firmware;
      };

      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [
          python3
          cmake
          ninja
          gcc-arm-embedded
          mpremote
          picotool
          ruff
        ];

        PICO_SDK_PATH = "${picoSdk}/lib/pico-sdk";

        shellHook = ''
          echo "Pico W HUB75 Matrix Receiver dev shell (PICO_SDK_PATH=$PICO_SDK_PATH)"
        '';
      };
    };
}
