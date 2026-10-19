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
          MATRIX_LD_LIBRARY_PATH = matrixLibraryPath;
          packages = ciPackages;
        };

        # Protected CI operations: the CI tools plus registry, cache, and
        # release clients, without the virtualization and GUI closure (qemu,
        # libvirt, cloud-utils) that every operation container would
        # otherwise copy from Attic before doing any work.
        devShells.ops = pkgs.mkShell {
          MATRIX_LD_LIBRARY_PATH = matrixLibraryPath;
          packages = ciPackages ++ opsPackages;
        };

        devShells.default = pkgs.mkShell {
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
            host = self.nixosConfigurations.homelab-01.config;
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
            host = self.nixosConfigurations.homelab-01.config;
          in
          pkgs.runCommand "agent-workspace-host-reservations" { } ''
            test ${nixpkgs.lib.escapeShellArg (builtins.toJSON host.services.k3s.extraFlags)} = ${
              nixpkgs.lib.escapeShellArg (
                builtins.toJSON [
                  "--node-ip=10.0.30.11"
                  "--advertise-address=10.0.30.11"
                  "--flannel-backend=none"
                  "--disable-network-policy"
                  "--cluster-cidr=10.42.0.0/16"
                  "--service-cidr=10.43.0.0/16"
                  "--disable-kube-proxy"
                  "--disable=servicelb"
                  "--disable=traefik"
                  "--disable=local-storage"
                  "--kubelet-arg=container-log-max-size=10Mi"
                  "--kubelet-arg=container-log-max-files=5"
                  "--kubelet-arg=image-gc-high-threshold=70"
                  "--kubelet-arg=image-gc-low-threshold=60"
                  "--kubelet-arg=eviction-hard=memory.available<500Mi,nodefs.available<2Gi,imagefs.available<15Gi"
                  "--etcd-snapshot-dir=/persist/etcd-snapshots"
                  "--etcd-snapshot-retention=14"
                  "--etcd-snapshot-schedule-cron=0 3 * * *"
                  "--kubelet-arg=system-reserved=cpu=2,memory=10Gi"
                  "--kubelet-arg=kube-reserved=cpu=500m,memory=1Gi"
                ]
              )
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
