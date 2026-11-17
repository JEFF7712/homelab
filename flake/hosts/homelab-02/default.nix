{ fleetInventory, ... }:
{
  imports = [
    ./hardware-configuration.nix
    ../../modules/disko-single-disk.nix
    ../../modules/k3s-joiner.nix
  ];

  homelab.disk.device = "/dev/disk/by-id/ata-FORESEE_64GB_SSD_0000007520__FMA39721";

  homelab.joiner = {
    enable = true;
    hostName = "homelab-02";
    primaryInterface = "enp1s0";
    nodeIp = fleetInventory.hosts.homelab-02.address;
    serverAddress = fleetInventory.k3sEndpoint;
  };

  # Cilium's DNS proxy receives pod DNS that an L7 policy redirects to it by
  # TPROXY, marked 0x200 in 0xf00. The k3s firewall admits pod sources only to
  # a few host ports, so it dropped that traffic and every lookup under an
  # FQDN policy timed out (gate 4, 2026-11-09). Only the orchestrator's
  # workers use such a policy, and they run here; the rule is on this host
  # alone so no pod traffic reaches host sockets on the enforcement host.
  networking.firewall.extraInputRules = ''
    meta mark & 0x00000f00 == 0x00000200 accept comment "cilium L7 proxy redirects"
  '';
}
