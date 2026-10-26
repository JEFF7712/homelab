{
  nixpkgs,
  system,
}:
let
  pkgs = nixpkgs.legacyPackages.${system};
  manifest = pkgs.writeText "agent-workspace-packet-flow.json" (
    builtins.toJSON {
      schema_version = 1;
      host_limits.homelab-01 = {
        memory_mib = 4096;
        vcpus = 4;
        cpu_percent = 200;
        io_weight = 1000;
      };
      workspaces =
        let
          workspace = {
            id = "packet-test";
            enabled = true;
            host = "homelab-01";
            owner = "test";
            trust_class = "personal";
            guest_username = "agent";
            browser_identity = "packet-test";
            ssh_authorized_keys = [ "ssh-ed25519 AAAATEST test" ];
            secret_references = [ ];
            image = {
              url = "https://example.invalid/image.qcow2";
              sha256 = builtins.concatStringsSep "" (builtins.genList (_: "0") 64);
            };
            resources = {
              memory_mib = 1024;
              vcpus = 1;
              cpu_percent = 100;
              io_weight = 100;
            };
            storage = {
              system_disk = "/persist/agent-workspaces/packet-test/disks/system.qcow2";
              data_disk = "/persist/agent-workspaces/packet-test/disks/data.qcow2";
              system_disk_gib = 16;
              data_disk_gib = 8;
            };
            network = {
              segment = "192.0.2.0/30";
              address = "192.0.2.2/30";
              gateway = "192.0.2.1";
              bridge = "aw-test-br";
              tap = "aw-test-tap";
              mac = "52:54:00:00:00:01";
              uplink = "wan-test";
              ha_api = "198.51.100.10";
              dns = [ "1.1.1.1" ];
              ntp = [ "1.1.1.1" ];
              ingress_sources = [ "198.51.100.0/24" ];
            };
          };
        in
        [
          workspace
          (
            workspace
            // {
              id = "other-test";
              network = workspace.network // {
                segment = "192.0.2.4/30";
                address = "192.0.2.6/30";
                gateway = "192.0.2.5";
                bridge = "aw-other-br";
                tap = "aw-other-tap";
                mac = "52:54:00:00:00:02";
              };
            }
          )
        ];
    }
  );
in
pkgs.testers.nixosTest {
  name = "agent-workspace-packet-flow";

  nodes.machine = {
    imports = [ ../modules/agent-workspace-network.nix ];
    networking.hostName = "homelab-01";
    networking.nftables.enable = true;
    networking.useNetworkd = true;
    services.agent-workspace-network = {
      enable = true;
      manifestFile = manifest;
      tcpEgress.packet-test = [
        {
          address = "10.10.10.2";
          ports = [
            2222
            8080
          ];
        }
        {
          address = "203.0.113.1";
          ports = [ 8000 ];
        }
      ];
    };
    # 8000 is closed in the NixOS firewall, so only the grant can admit it;
    # 8001 is open, so only the workspace policy can deny it.
    networking.firewall.allowedTCPPorts = [ 8001 ];
    environment.systemPackages = [
      pkgs.curl
      pkgs.iproute2
      pkgs.python3
    ];
    system.stateVersion = "26.05";
  };

  testScript = ''
    machine.start()
    machine.wait_for_unit("nftables.service")
    machine.wait_until_succeeds("ip link show aw-test-br")
    machine.succeed("ip netns add guest")
    machine.succeed("ip netns add public")
    machine.succeed("ip link add aw-test-tap type veth peer name guest-ns")
    machine.succeed("ip link set guest-ns address 52:54:00:00:00:01")
    machine.succeed("ip link set aw-test-tap master aw-test-br")
    machine.succeed("ip link set aw-test-tap up")
    machine.succeed("ip link set guest-ns netns guest")
    machine.succeed("ip -n guest link set lo up")
    machine.succeed("ip -n guest link set guest-ns up")
    machine.succeed("ip -n guest address add 192.0.2.2/30 dev guest-ns")
    machine.succeed("ip -n guest route add default via 192.0.2.1")
    machine.succeed("ip netns add other")
    machine.succeed("ip link add aw-other-tap type veth peer name other-ns")
    machine.succeed("ip link set other-ns address 52:54:00:00:00:02")
    machine.succeed("ip link set aw-other-tap master aw-other-br")
    machine.succeed("ip link set aw-other-tap up")
    machine.succeed("ip link set other-ns netns other")
    machine.succeed("ip -n other link set lo up")
    machine.succeed("ip -n other link set other-ns up")
    machine.succeed("ip -n other address add 192.0.2.6/30 dev other-ns")
    machine.succeed("ip -n other route add default via 192.0.2.5")
    machine.succeed("ip link add wan-test type veth peer name public-ns")
    machine.succeed("ip address add 203.0.113.1/30 dev wan-test")
    machine.succeed("ip link set wan-test up")
    machine.succeed("ip link set public-ns netns public")
    machine.succeed("ip -n public link set lo up")
    machine.succeed("ip -n public link set public-ns up")
    machine.succeed("ip -n public address add 203.0.113.2/30 dev public-ns")
    machine.succeed("ip -n public address add 10.10.10.2/32 dev public-ns")
    machine.succeed("ip -n public address add 10.10.10.3/32 dev public-ns")
    machine.succeed("ip route add 10.10.10.0/24 via 203.0.113.2 dev wan-test")
    machine.succeed("ip -n public route add default via 203.0.113.1")
    machine.succeed("ip netns exec public python3 -m http.server 8080 --bind 203.0.113.2 >/tmp/http.log 2>&1 &")
    machine.wait_until_succeeds("ip netns exec public curl --fail --silent http://203.0.113.2:8080/")
    machine.succeed("ip netns exec guest curl --fail --silent --max-time 5 http://203.0.113.2:8080/")
    machine.succeed("ip netns exec other curl --fail --silent --max-time 5 http://203.0.113.2:8080/")
    for address, port in [("10.10.10.2", 2222), ("10.10.10.2", 8080), ("10.10.10.2", 9090), ("10.10.10.3", 8080)]:
        machine.succeed(f"ip netns exec public python3 -m http.server {port} --bind {address} >/tmp/http-{address}-{port}.log 2>&1 &")
        machine.wait_until_succeeds(f"ip netns exec public curl --fail --silent http://{address}:{port}/")
    for port in [2222, 8080]:
        machine.succeed(f"ip netns exec guest curl --fail --silent --max-time 5 http://10.10.10.2:{port}/")
        machine.fail(f"ip netns exec other curl --fail --silent --max-time 1 http://10.10.10.2:{port}/")
    machine.fail("ip netns exec guest curl --fail --silent --max-time 1 http://10.10.10.2:9090/")
    machine.fail("ip netns exec guest curl --fail --silent --max-time 1 http://10.10.10.3:8080/")
    for port in [8000, 8001]:
        machine.succeed(f"python3 -m http.server {port} --bind 203.0.113.1 >/tmp/http-host-{port}.log 2>&1 &")
        machine.wait_until_succeeds(f"curl --fail --silent http://203.0.113.1:{port}/")
    machine.succeed("ip netns exec guest curl --fail --silent --max-time 5 http://203.0.113.1:8000/")
    machine.fail("ip netns exec guest curl --fail --silent --max-time 1 http://203.0.113.1:8001/")
    machine.fail("ip netns exec other curl --fail --silent --max-time 1 http://203.0.113.1:8000/")
    machine.succeed("systemctl stop nftables.service")
    machine.succeed("test $(cat /sys/class/net/aw-test-tap/operstate) = down")
    machine.succeed("test $(cat /sys/class/net/aw-other-tap/operstate) = down")
    machine.fail("ip netns exec guest curl --fail --silent --max-time 1 http://203.0.113.2:8080/")
  '';
}
