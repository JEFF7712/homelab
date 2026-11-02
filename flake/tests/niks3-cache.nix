{
  nixpkgs,
  impermanence,
  niks3,
  system,
}:
let
  pkgs = nixpkgs.legacyPackages.${system};
  payload = pkgs.hello;
in
pkgs.testers.nixosTest {
  name = "niks3-cache-service-contract";
  nodes.machine =
    { config, lib, ... }:
    {
      virtualisation.host.pkgs = pkgs.lib.mkForce (import ./vm-host-pkgs.nix { inherit pkgs; });
      imports = [
        impermanence.nixosModules.impermanence
        niks3.nixosModules.niks3
        ../modules/niks3-cache.nix
      ];
      homelab.niks3Cache = {
        enable = true;
        capacity = "1G";
      };
      virtualisation.fileSystems."/persist" = {
        device = "none";
        fsType = "tmpfs";
        neededForBoot = true;
      };
      virtualisation.fileSystems."/srv/nix-cache" = {
        device = "none";
        fsType = "tmpfs";
      };
      nix.settings.experimental-features = [ "nix-command" ];
      environment.systemPackages = [ config.services.niks3.package ];
      system.extraDependencies = [ payload ];
      virtualisation.memorySize = 2048;
      system.stateVersion = "26.05";
    };
  testScript = ''
    payload = "${payload}"
    cache = "http://127.0.0.1:8090"

    machine.start()
    machine.wait_for_unit("niks3.service")
    machine.wait_for_open_port(8090)

    # The cache's Garage stays on loopback; only niks3 is reachable.
    machine.fail("ss -ltnH | grep -E '(0\\.0\\.0\\.0|\\*|\\[::\\]):39(10|11) '")

    def provisioned():
        return {
            name: machine.succeed(f"cat /persist/niks3/{name}")
            for name in ["signing-key.pub", "s3-access-key", "api-token"]
        }

    first = provisioned()
    assert first["signing-key.pub"].startswith("homelab-niks3-1:"), first["signing-key.pub"]

    machine.succeed(
        f"niks3 push --server-url {cache} --auth-token-path /persist/niks3/api-token {payload}"
    )

    # Restore the closure into an empty store, trusting only the cache key.
    machine.succeed(
        f"nix copy --from {cache} --to /tmp/restore "
        f"--option trusted-public-keys '{first['signing-key.pub'].strip()}' {payload}"
    )
    machine.succeed(f"test -x /tmp/restore{payload}/bin/hello")

    # Provisioning is idempotent: a restart keeps the key, credentials and data.
    machine.succeed("systemctl restart niks3-provision.service niks3.service")
    machine.wait_for_open_port(8090)
    assert provisioned() == first
    narinfo = payload.split("/")[-1].split("-")[0]
    machine.succeed(f"curl -sf {cache}/{narinfo}.narinfo")
  '';
}
