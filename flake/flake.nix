{
  description = "NixOS homelab development and validation tools";

  nixConfig = {
    extra-substituters = [ "http://10.0.30.20:8080/homelab" ];
    extra-trusted-public-keys = [ "homelab:J+OVQOCG2sNT2KoVbWGPikoWcIbBanHnY2NOcMF3vwk=" ];
  };

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    impermanence.url = "github:nix-community/impermanence";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      disko,
      impermanence,
      ...
    }:
    {
      nixosModules.projectctl = import ./modules/projectctl.nix;
      nixosConfigurations =
        let
          mkHost =
            hostName: extraModules:
            nixpkgs.lib.nixosSystem {
              system = "x86_64-linux";
              modules = [
                disko.nixosModules.disko
                impermanence.nixosModules.impermanence
                (./hosts + "/${hostName}")
              ]
              ++ extraModules;
            };
          baseHosts = [
            "adguard-netbird-01"
            "nas-01"
            "homelab-01"
            "homelab-02"
            "homelab-03"
            "homelab-04"
            "homelab-05"
          ];
          registryHosts = [
            "homelab-01"
            "homelab-02"
            "homelab-03"
            "homelab-04"
            "homelab-05"
          ];
        in
        builtins.listToAttrs (
          map (hostName: nixpkgs.lib.nameValuePair hostName (mkHost hostName [ ])) baseHosts
        )
        // {
          homelab-04-dr = mkHost "homelab-04" [ { homelab.gitlabDR.enable = true; } ];
        }
        // builtins.listToAttrs (
          map (
            hostName:
            nixpkgs.lib.nameValuePair "${hostName}-registry" (
              mkHost hostName [ ./modules/k3s-registry-client.nix ]
            )
          ) registryHosts
        );
    }
    // flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        vmCheck =
          check:
          check.extend {
            # QEMU falls back to TCG when sandbox jobs have no host KVM device.
            modules = [ { requiredFeatures.kvm = false; } ];
          };
        python = pkgs.python313.withPackages (pythonPackages: [
          pythonPackages.bcrypt
          pythonPackages.pyyaml
          pythonPackages.jinja2
          pythonPackages.cryptography
        ]);
        matrixLibraryPath = pkgs.lib.optionalString pkgs.stdenv.hostPlatform.isLinux (
          pkgs.lib.makeLibraryPath [
            pkgs.stdenv.cc.cc.lib
            pkgs.zlib
          ]
        );
        opsPackages = with pkgs; [
          attic-client
          cosign
          curl
          dnsutils
          getent
          nixos-rebuild-ng
          oras
          skopeo
          tea
        ];
        ciPackages = with pkgs; [
          # Scoped validation lanes run unit tests directly in this shell,
          # not inside checks.repository-contract, so they need the same
          # test tools: node for the client plugin tests, htpasswd, php, and
          # patchelf for registry, OPNsense, and zot fixtures.
          apacheHttpd
          nodejs
          patchelf
          php
          age
          git
          gitleaks
          # hooks/common.sh treats a missing jq as "cannot read hook input"
          # and answers every hook with a bare {}, so without this the agent
          # harness tests pass vacuously in CI instead of testing the hooks.
          jq
          just
          kubeconform
          kubectl
          kubernetes-helm
          nixfmt
          (import ../nix/ci-providers.nix { inherit pkgs; })
          pyright
          python
          ruff
          shellcheck
          sops
          # The full gate's matrix check syncs the frozen service env with uv.
          uv
          yamllint
          # The registry serves this exact build and the GC fixture asserts
          # the version it runs, so both share nix/zot-release.nix.
          (import ../nix/zot-release.nix { inherit pkgs lib; }).zotPackage
        ];
      in
      {
        packages.projectctl = import ./packages/projectctl.nix { inherit pkgs; };
        formatter = pkgs.nixfmt;

        devShells.voice-asr =
          let
            gpuPkgs = import nixpkgs {
              inherit system;
              config.allowUnfree = true;
            };
          in
          gpuPkgs.mkShell.override { stdenv = gpuPkgs.cudaPackages.backendStdenv; } {
            packages = with gpuPkgs; [
              cmake
              ninja
              pkg-config
              sentencepiece
              cudaPackages.cudatoolkit
              python312
              git
            ];
          };

        devShells.ci = pkgs.mkShell {
          HOMELAB_DEV_SHELL = "ci";
          MATRIX_LD_LIBRARY_PATH = matrixLibraryPath;
          packages = ciPackages;
        };

        # Protected CI operations: the CI tools plus registry, cache, and
        # release clients, without the virtualization and GUI closure (qemu,
        # libvirt, cloud-utils) that every operation container would
        # otherwise copy from Attic before doing any work.
        devShells.ops = pkgs.mkShell {
          HOMELAB_DEV_SHELL = "ops";
          MATRIX_LD_LIBRARY_PATH = matrixLibraryPath;
          packages = ciPackages ++ opsPackages;
        };

        devShells.default = pkgs.mkShell {
          HOMELAB_DEV_SHELL = "default";
          MATRIX_LD_LIBRARY_PATH = matrixLibraryPath;
          packages =
            ciPackages
            ++ opsPackages
            ++ (with pkgs; [
              cloud-utils
              glab
              libvirt
              prometheus.cli
              qemu_kvm
            ]);
        };

        checks.repository-contract =
          pkgs.runCommand "repository-contract"
            {
              nativeBuildInputs = [
                pkgs.apacheHttpd
                pkgs.bash
                pkgs.coreutils
                pkgs.git
                pkgs.gitleaks
                pkgs.jq
                pkgs.just
                pkgs.kubeconform
                pkgs.kubectl
                pkgs.libvirt
                pkgs.nodejs
                pkgs.patchelf
                pkgs.php
                python
              ];
            }
            ''
              cd ${../.}
              python -m scripts.ci.tests --output "$out/tests.xml" --log-report --exclude-module test_zot_registry_module
            '';

        checks.agent-workspace-network =
          let
            host = self.nixosConfigurations.homelab-04.config;
            policy = host.networking.nftables.tables.agent-workspaces.content;
            bridges = builtins.attrNames host.systemd.network.netdevs;
          in
          pkgs.runCommand "agent-workspace-network-evaluation" { } ''
            test ${nixpkgs.lib.escapeShellArg (builtins.toJSON bridges)} = '["40-rupan","40-sam"]'
            ${
              if nixpkgs.lib.hasInfix "aw-rupan-br" policy && nixpkgs.lib.hasInfix "aw-sam-br" policy then
                "true"
              else
                "false"
            }
            ${if !(nixpkgs.lib.hasInfix "dport 443" policy) then "true" else "false"}
            ${if !(nixpkgs.lib.hasInfix "dport 123" policy) then "true" else "false"}
            touch $out
          '';

        checks.agent-workspace-packet-flow = vmCheck (
          import ./tests/agent-workspace-packet-flow.nix {
            inherit nixpkgs system;
          }
        );

        checks.agent-workspace-host-reservations =
          let
            host = self.nixosConfigurations.homelab-04.config;
          in
          pkgs.runCommand "agent-workspace-host-reservations" { } ''
            ${
              if
                builtins.elem "--kubelet-arg=system-reserved=cpu=3,memory=12288Mi" host.services.k3s.extraFlags
              then
                "true"
              else
                "false"
            }
            test ${
              nixpkgs.lib.escapeShellArg host.systemd.slices."machine-agent\\x2dworkspaces".sliceConfig.CPUQuota
            } = 200%
            test ${
              nixpkgs.lib.escapeShellArg host.systemd.slices."machine-agent\\x2dworkspaces".sliceConfig.MemoryMax
            } = 10240M
            touch $out
          '';

        checks.agent-workspace-libvirt-normalization = vmCheck (
          import ./tests/agent-workspace-libvirt-normalization.nix {
            inherit nixpkgs system;
          }
        );

        checks.kiosk-tty1 =
          let
            host = self.nixosConfigurations.homelab-05.config;
          in
          assert !host.systemd.units."getty@tty1.service".enable;
          assert !host.systemd.units."autovt@tty1.service".enable;
          assert builtins.elem "multi-user.target" host.systemd.services.cage-tty1.wantedBy;
          assert host.systemd.services.cage-tty1.serviceConfig.Restart == "always";
          pkgs.runCommand "kiosk-tty1-evaluation" { } ''
            touch $out
          '';

        checks.zot-registry = self.nixosConfigurations.nas-01.config.services.homelab-zot-registry.package;
      }
    );
}
