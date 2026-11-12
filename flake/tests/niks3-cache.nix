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
  nodes.server =
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
        address = config.networking.primaryIPAddress;
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
      virtualisation.memorySize = 2048;
      system.stateVersion = "26.05";
    };
  # Pushing clients upload NARs straight to the server's Garage through
  # presigned URLs, so the push must come from another machine.
  nodes.client =
    { nodes, ... }:
    {
      virtualisation.host.pkgs = pkgs.lib.mkForce (import ./vm-host-pkgs.nix { inherit pkgs; });
      nix.settings.experimental-features = [ "nix-command" ];
      environment.systemPackages = [
        nodes.server.services.niks3.package
        pkgs.curl
      ];
      system.extraDependencies = [ payload ];
      virtualisation.memorySize = 1024;
      system.stateVersion = "26.05";
    };
  testScript = ''
    payload = "${payload}"
    cache = "http://server:8090"

    start_all()
    server.wait_for_unit("niks3.service")
    server.wait_for_open_port(8090)

    # Garage RPC stays on loopback; only the cache and the S3 API are exposed.
    server.fail("ss -ltnH | grep -E '(0\\.0\\.0\\.0|\\*|\\[::\\]):3911 '")

    def provisioned():
        return {
            name: server.succeed(f"cat /persist/niks3/{name}")
            for name in ["signing-key.pub", "s3-access-key", "api-token"]
        }

    first = provisioned()
    assert first["signing-key.pub"].startswith("homelab-niks3-1:"), first["signing-key.pub"]

    client.wait_for_unit("multi-user.target")
    client.succeed(f"install -m 0400 /dev/stdin /tmp/token <<< '{first['api-token'].strip()}'")
    client.succeed(f"niks3 push --server-url {cache} --auth-token-path /tmp/token {payload}")

    # Restore the closure into an empty store, trusting only the cache key.
    client.succeed(
        f"nix copy --from {cache} --to /tmp/restore "
        f"--option trusted-public-keys '{first['signing-key.pub'].strip()}' {payload}"
    )
    client.succeed(f"test -x /tmp/restore{payload}/bin/hello")

    # Provisioning is idempotent: a restart keeps the key, credentials and data.
    server.succeed("systemctl restart niks3-provision.service niks3.service")
    server.wait_for_open_port(8090)
    assert provisioned() == first
    narinfo = payload.split("/")[-1].split("-")[0]
    client.succeed(f"curl -sf {cache}/{narinfo}.narinfo")
  '';
}
