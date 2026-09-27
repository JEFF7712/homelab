{ pkgs, ... }:
{
  imports = [
    ./adguard-netbird/adguard.nix
    ./adguard-netbird/netbird.nix
    ./adguard-netbird/mosquitto.nix
    ./adguard-netbird/zigbee2mqtt.nix
    ./adguard-netbird/roku-bridge.nix
    ./adguard-netbird/roku-cloud-bridge.nix
    ./adguard-netbird/ledfx-roku-bridge.nix
  ];

  boot = {
    loader = {
      efi.canTouchEfiVariables = true;
      systemd-boot.enable = true;
    };
  };

  # Box firewall. Port owners live in the service modules:
  # adguard.nix (53, 3000, 9100 via node exporter below), netbird.nix (51820),
  # mosquitto.nix (1883), ledfx-roku-bridge.nix (9000/udp from homelab-05);
  # 22 is base SSH access.
  networking = {
    useDHCP = false;
    firewall = {
      enable = true;
      allowedUDPPorts = [ 51820 ];
      extraInputRules = ''
        ip saddr 10.0.10.0/24 tcp dport { 22, 3000 } accept
        ip saddr 10.0.10.0/24 tcp dport 53 accept
        ip saddr 10.0.10.0/24 udp dport 53 accept
        ip saddr { 10.0.30.14, 10.0.30.20 } tcp dport 22 accept
        ip saddr 10.0.30.0/24 tcp dport 53 accept
        ip saddr 10.0.30.0/24 udp dport 53 accept
        ip saddr { 10.0.30.11, 10.0.30.12, 10.0.30.13, 10.0.30.14, 10.0.30.15 } tcp dport 1883 accept
        ip saddr 10.0.30.15 udp dport 9000 accept
        ip saddr 10.0.30.0/24 tcp dport 9100 accept
        iifname "wt0" tcp dport { 22, 53, 3000 } accept
        iifname "wt0" udp dport 53 accept
      '';
    };
  };

  systemd.network = {
    enable = true;
    netdevs."20-netbird-vlan".netdevConfig = {
      Kind = "vlan";
      Name = "netbird-vlan";
    };
    netdevs."20-netbird-vlan".vlanConfig.Id = 60;
    networks = {
      "30-infrastructure" = {
        matchConfig.Name = "enp1s0";
        networkConfig = {
          Address = "10.0.30.10/24";
          DNS = "10.0.30.1";
          Gateway = "10.0.30.1";
          VLAN = "netbird-vlan";
        };
        linkConfig.RequiredForOnline = "routable";
      };
      "60-netbird-policy" = {
        matchConfig.Name = "netbird-vlan";
        networkConfig.Address = "10.0.60.2/24";
      };
    };
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/private 0700 root root -"
    "d /persist/etc/ssh 0700 root root -"
    "d /persist/secrets 0700 root root -"
  ];

  environment.persistence."/persist" = {
    hideMounts = true;
    directories = [
      "/var/lib/nixos"
      "/var/lib/systemd"
    ];
    files = [ "/etc/machine-id" ];
  };

  environment.systemPackages = with pkgs; [
    btrfs-progs
    cryptsetup
    curl
    ethtool
    tcpdump
  ];

  services.prometheus.exporters.node = {
    enable = true;
    port = 9100;
    listenAddress = "0.0.0.0";
    openFirewall = false;
    enabledCollectors = [
      "filesystem"
      "systemd"
    ];
    extraFlags = [
      "--collector.systemd.unit-include=(adguardhome|netbird|roku-bridge|ledfx-roku-bridge|mosquitto)\\.(service|timer)"
    ];
  };
}
