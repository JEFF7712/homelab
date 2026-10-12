{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.omarchyVm;
  root = "/persist/omarchy-vm/akshath";
  python = "${pkgs.python3}/bin/python3";
  runtime = ./omarchy_vm/runtime.py;
  firmware = pkgs.OVMF;
  privateRanges = "{ 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.0.2.0/24, 192.168.0.0/16, 198.18.0.0/15, 198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 240.0.0.0/4 }";
  ingress = "{ 10.0.10.0/24, 10.0.20.0/24, 10.0.30.10/32, 100.64.0.0/10 }";
  launch = pkgs.writeShellScript "omarchy-akshath-launch" ''
    set -euo pipefail
    test -f ${root}/system.qcow2
    test -f ${root}/OVMF_VARS.fd
    test "$(${pkgs.qemu}/bin/qemu-img info --output=json ${root}/system.qcow2 | ${pkgs.jq}/bin/jq -r '."virtual-size"')" = "${
      toString (cfg.diskGiB * 1073741824)
    }"
    media=()
    if [[ -f ${root}/installing ]]; then
      test -f ${root}/cidata.iso
      test -f ${root}/omarchy-4.0.4.iso
      media=(-drive file=${root}/omarchy-4.0.4.iso,media=cdrom,readonly=on,cache=none -drive file=${root}/cidata.iso,media=cdrom,readonly=on,cache=none)
    fi
    exec ${pkgs.qemu}/bin/qemu-system-x86_64 \
      -name omarchy-akshath -machine q35,accel=kvm -cpu host \
      -smp ${toString cfg.vcpus} -m ${toString cfg.memoryMiB} \
      -drive if=pflash,format=raw,readonly=on,file=${firmware.firmware} \
      -drive if=pflash,format=raw,file=${root}/OVMF_VARS.fd \
      -drive file=${root}/system.qcow2,format=qcow2,if=virtio,cache=none,discard=unmap \
      -boot order=cd -device virtio-vga -device qemu-xhci -device usb-tablet \
      -netdev tap,id=net0,ifname=tap-akshath,script=no,downscript=no \
      -device virtio-net-pci,netdev=net0,mac=02:60:00:00:00:02 \
      -device virtio-rng-pci -device virtio-serial-pci \
      -chardev socket,path=/run/omarchy-akshath/guest-agent.sock,server=on,wait=off,id=agent \
      -device virtserialport,chardev=agent,name=org.qemu.guest_agent.0 \
      -display none -vnc 10.250.60.1:6 \
      -qmp unix:/run/omarchy-akshath/qmp.sock,server=on,wait=off \
      -serial file:${root}/serial.log "''${media[@]}"
  '';
in
{
  options.homelab.omarchyVm = {
    enable = lib.mkEnableOption "Akshath's isolated Omarchy KVM guest";
    vcpus = lib.mkOption {
      type = lib.types.ints.between 1 6;
      default = 4;
    };
    memoryMiB = lib.mkOption {
      type = lib.types.ints.between 2048 12288;
      default = 8192;
    };
    diskGiB = lib.mkOption {
      type = lib.types.ints.between 40 200;
      default = 100;
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = config.networking.useNetworkd;
        message = "Omarchy VM requires networkd.";
      }
    ];
    boot.kernelModules = [
      "tun"
      "kvm-intel"
    ];
    boot.kernel.sysctl."net.ipv4.ip_forward" = 1;
    users.groups.omarchy-vm.gid = 995;
    users.users.omarchy-vm = {
      isSystemUser = true;
      uid = 995;
      group = "omarchy-vm";
      extraGroups = [ "kvm" ];
    };
    systemd.tmpfiles.rules = [
      "d ${root} 0700 omarchy-vm omarchy-vm - -"
      "z ${root}/system.qcow2 0600 omarchy-vm omarchy-vm - -"
      "z ${root}/OVMF_VARS.fd 0600 omarchy-vm omarchy-vm - -"
      "z ${root}/cidata.iso 0400 omarchy-vm omarchy-vm - -"
      "z ${root}/omarchy-4.0.4.iso 0400 omarchy-vm omarchy-vm - -"
    ];
    systemd.network.netdevs = {
      "40-akshath".netdevConfig = {
        Kind = "bridge";
        Name = "br-akshath";
      };
      "41-akshath-tap" = {
        netdevConfig = {
          Kind = "tap";
          Name = "tap-akshath";
        };
        tapConfig = {
          User = "omarchy-vm";
          Group = "omarchy-vm";
        };
      };
    };
    systemd.network.networks = {
      "40-akshath" = {
        matchConfig.Name = "br-akshath";
        address = [ "10.250.60.1/30" ];
        networkConfig = {
          DHCPServer = true;
          IPv6AcceptRA = false;
          LinkLocalAddressing = "no";
          ConfigureWithoutCarrier = true;
        };
        dhcpServerConfig = {
          PoolOffset = 2;
          PoolSize = 1;
          DNS = "1.1.1.1 1.0.0.1";
          EmitNTP = true;
          NTP = "162.159.200.1";
        };
        dhcpServerStaticLeases = [
          {
            MACAddress = "02:60:00:00:00:02";
            Address = "10.250.60.2";
          }
        ];
        linkConfig.RequiredForOnline = "no";
      };
      "41-akshath-tap" = {
        matchConfig.Name = "tap-akshath";
        networkConfig = {
          Bridge = "br-akshath";
          LinkLocalAddressing = "no";
          IPv6AcceptRA = false;
        };
        linkConfig.RequiredForOnline = "no";
      };
    };
    networking.nftables.tables.omarchy-akshath = {
      family = "inet";
      content = ''
        chain input {
          type filter hook input priority -10; policy accept;
          iifname "br-akshath" ether saddr != 02:60:00:00:00:02 counter drop
          iifname "br-akshath" meta nfproto ipv6 counter drop
          iifname "br-akshath" ip saddr 0.0.0.0 udp sport 68 udp dport 67 accept
          iifname "br-akshath" ip saddr != 10.250.60.2 counter drop
          iifname "br-akshath" ct state established,related accept
          iifname "br-akshath" udp sport 68 udp dport 67 accept
          iifname "br-akshath" tcp dport 5906 accept
          iifname "br-akshath" counter drop
        }
        chain forward {
          type filter hook forward priority -10; policy accept;
          iifname "br-akshath" ether saddr != 02:60:00:00:00:02 counter drop
          iifname "br-akshath" meta nfproto ipv6 counter drop
          iifname "br-akshath" ip saddr != 10.250.60.2 counter drop
          iifname "br-akshath" ct state invalid counter drop
          iifname "br-akshath" ct state established,related accept
          iifname "br-akshath" ip daddr ${privateRanges} counter drop
          iifname "br-akshath" oifname "enp0s31f6" accept
          iifname "br-akshath" counter drop
          oifname "br-akshath" ct state established,related accept
          oifname "br-akshath" ip saddr ${ingress} ip daddr 10.250.60.2 tcp dport 22 accept
          oifname "br-akshath" counter drop
        }
        chain prerouting {
          type nat hook prerouting priority dstnat; policy accept;
          iifname "enp0s31f6" ip saddr ${ingress} ip daddr 10.0.30.15 tcp dport 2222 dnat ip to 10.250.60.2:22
        }
        chain postrouting {
          type nat hook postrouting priority srcnat; policy accept;
          ip saddr 10.250.60.2 oifname "enp0s31f6" masquerade
        }
      '';
    };
    networking.firewall.extraInputRules = lib.mkAfter ''
      iifname "br-akshath" ip saddr { 0.0.0.0, 10.250.60.2 } udp sport 68 udp dport 67 accept
      iifname "br-akshath" ip saddr 10.250.60.2 tcp dport 5906 accept
      ip saddr { 10.0.30.0/24, 10.42.0.0/16 } tcp dport 9126 accept
    '';
    networking.firewall.extraReversePathFilterRules = lib.mkAfter ''
      iifname "br-akshath" ip saddr { 0.0.0.0, 10.250.60.2 } accept
    '';
    systemd.services.omarchy-akshath = {
      description = "Akshath Omarchy VM (4 vCPUs, persistent disk)";
      wantedBy = [ "multi-user.target" ];
      requires = [
        "nftables.service"
        "systemd-networkd.service"
      ];
      after = [
        "nftables.service"
        "systemd-networkd.service"
        "systemd-tmpfiles-setup.service"
        "systemd-tmpfiles-resetup.service"
      ];
      bindsTo = [ "nftables.service" ];
      unitConfig.ConditionPathExists = "${root}/system.qcow2";
      serviceConfig = {
        User = "omarchy-vm";
        Group = "omarchy-vm";
        RuntimeDirectory = "omarchy-akshath";
        RuntimeDirectoryMode = "0700";
        ExecStart = launch;
        ExecStop = "${python} ${runtime} stop --socket /run/omarchy-akshath/qmp.sock";
        Restart = "on-failure";
        RestartSec = "10s";
        TimeoutStopSec = "120s";
        CPUQuota = "${toString (cfg.vcpus * 100)}%";
        CPUWeight = 50;
        MemoryAccounting = true;
        MemoryHigh = "${toString (cfg.memoryMiB + 512)}M";
        MemoryMax = "${toString (cfg.memoryMiB + 1024)}M";
        MemorySwapMax = "0";
        IOAccounting = true;
        IOWeight = 50;
        UMask = "0077";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ReadWritePaths = [ root ];
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictSUIDSGID = true;
        CapabilityBoundingSet = "";
        DevicePolicy = "closed";
        DeviceAllow = [
          "/dev/kvm rw"
          "/dev/net/tun rw"
        ];
      };
    };
    systemd.services.omarchy-akshath-metrics = {
      description = "Host-enforced VM metrics and guest node exporter proxy";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      serviceConfig = {
        User = "omarchy-vm";
        Group = "omarchy-vm";
        ExecStart = "${python} ${runtime} serve --disk ${root}/system.qcow2 --vcpus ${toString cfg.vcpus} --memory-mib ${toString cfg.memoryMiB} --disk-gib ${toString cfg.diskGiB}";
        Restart = "on-failure";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        MemoryMax = "128M";
        CPUQuota = "10%";
        CapabilityBoundingSet = "";
      };
    };
    networking.nftables.enable = true;
    environment.systemPackages = [
      pkgs.qemu
      pkgs.cdrtools
    ];
    environment.etc."omarchy-vm/firmware-code".source = firmware.firmware;
    environment.etc."omarchy-vm/firmware-vars".source = firmware.variables;
  };
}
