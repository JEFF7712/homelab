{ config, fleetInventory, ... }:
{
  homelab = {
    networks = fleetInventory.networks;
    hosts = fleetInventory.hosts;
    devices = {
      spotify_matrix = {
        description = "Pico W Spotify matrix";
        hostname = "spotify-matrix";
        address = "10.0.20.136";
        mac = "28:cd:c1:0d:31:05";
        network = "clients";
      };
      laptop_nixos = {
        description = "Rupan's NixOS laptop";
        hostname = "laptop-nixos";
        address = "10.0.20.50";
        mac = "90:09:df:07:af:30";
        network = "clients";
      };
      ap_downstairs = {
        description = "Downstairs NETGEAR R6400v2 access point";
        hostname = "ap-downstairs";
        address = "10.0.20.3";
        mac = "80:cc:9c:21:88:e7";
        network = "clients";
      };
      bambu_a1 = {
        description = "Bambu Lab A1 3D printer";
        hostname = "bambu-a1";
        address = "10.0.20.124";
        mac = "28:84:85:4d:92:e8";
        network = "clients";
      };
      feit_switch_01 = {
        description = "Feit Electric smart switch";
        hostname = "feit-switch-01";
        address = "10.0.20.178";
        mac = "70:89:76:4d:66:e3";
        network = "clients";
      };
      tuya_switch_02 = {
        description = "Tuya smart switch";
        hostname = "tuya-switch-02";
        address = "10.0.20.170";
        mac = "70:89:76:5c:2d:7e";
        network = "clients";
      };
      roku_desk_lamp = {
        description = "Roku BC1000X desk lamp (LAN control, WAN blocked)";
        hostname = "roku-desk-lamp";
        address = "10.0.20.117";
        mac = "7c:67:ab:0a:83:ab";
        network = "clients";
      };
      roku_floor_lamp = {
        description = "Roku BC1000X floor lamp (LAN control, WAN blocked)";
        hostname = "roku-floor-lamp";
        address = "10.0.20.116";
        mac = "7c:67:ab:16:23:b7";
        network = "clients";
      };
      bus_display = {
        description = "Waveshare bus departure display";
        hostname = "bus-display";
        address = "10.0.20.120";
        mac = "14:c1:9f:59:cd:60";
        network = "clients";
      };
    };
    aliases = {
      spotify_matrix_senders = {
        type = "host";
        hosts = fleetInventory.k3sHosts;
        description = "Kubernetes nodes permitted to stream to the Pico matrix";
        enabled = true;
      };
      iot_bambu_a1 = {
        type = "host";
        description = "Bambu Lab A1 reserved LAN address";
        enabled = true;
        devices = [ "bambu_a1" ];
      };
      iot_tuya_sw01 = {
        type = "host";
        description = "Feit smart switch reserved LAN address";
        enabled = true;
        devices = [ "feit_switch_01" ];
      };
      iot_tuya_sw02 = {
        type = "host";
        description = "Tuya smart switch reserved LAN address";
        enabled = true;
        devices = [ "tuya_switch_02" ];
      };
      iot_bambu_ports = {
        type = "port";
        content = [
          "8883"
          "990"
          "2024"
          "2025"
          "6000"
        ];
        description = "Bambu Lab LAN mode service ports";
        enabled = true;
      };
      roku_bulbs_lo = {
        type = "network";
        content = [ "10.0.20.112/29" ];
        description = "Roku bulb local API segment";
        enabled = true;
      };
      roku_lan_bulbs = {
        type = "host";
        description = "Roku BC1000X lamps pinned to LAN-only control";
        enabled = true;
        devices = [
          "roku_floor_lamp"
          "roku_desk_lamp"
        ];
      };
      netbird_allowed_dests = {
        type = "network";
        content = [
          config.homelab.networks.management.cidr
          config.homelab.networks.clients.cidr
          config.homelab.networks.infrastructure.cidr
          config.homelab.networks.load-balancers.cidr
        ];
        description = "NetBird peers may reach management, clients, infrastructure, and load-balancer VIPs";
        enabled = true;
      };
      spotify_connect_ports = {
        type = "port";
        content = [
          "38801"
          "38802"
          "38803"
          "38804"
          "38805"
          "38806"
          "38807"
          "38808"
          "38809"
          "38810"
          "38901"
          "38902"
          "38903"
          "38904"
          "38905"
          "38906"
          "38907"
          "38908"
          "38909"
          "38910"
        ];
        description = "Spotify Connect zeroconf ports for Music Assistant players";
        enabled = true;
      };
      admin_laptop = {
        type = "host";
        description = "Rupan's laptop admin device on clients VLAN";
        enabled = true;
        devices = [ "laptop_nixos" ];
      };
      moonlight_clients = {
        type = "host";
        hosts = [
          "homelab-04"
          "homelab-05"
        ];
        description = "Moonlight streaming clients on infrastructure VLAN";
        enabled = true;
      };
      sunshine_ports = {
        type = "port";
        content = [
          "47984"
          "47989"
          "47998"
          "47999"
          "48000"
          "48002"
          "48010"
        ];
        description = "Sunshine game streaming server ports";
        enabled = true;
      };
      smb_ports = {
        type = "port";
        content = [ "445" ];
        description = "SMB2/3 network file sharing port";
        enabled = true;
      };
    };
    rules = {
      management-allow-any = {
        description = "Allow management VLAN to all destinations";
        enabled = true;
        sequence = 100;
        interface = {
          interface = [ "opt2" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.management.cidr;
            port = "";
          };
          destination = {
            net = "any";
            port = "";
          };
        };
      };
      clients-allow-dns = {
        description = "Allow clients to OPNsense DNS";
        enabled = true;
        sequence = 200;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP/UDP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.clients.cidr;
            port = "";
          };
          destination = {
            net = config.homelab.networks.clients.gateway;
            port = "53";
          };
        };
      };
      clients-allow-admin-laptop = {
        description = "Allow admin laptop to all destinations";
        enabled = true;
        sequence = 205;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = true;
          source = {
            net = "admin_laptop";
            port = "";
          };
          destination = {
            net = "any";
            port = "";
          };
        };
      };
      clients-allow-load-balancers = {
        description = "Allow clients to Kubernetes services";
        enabled = true;
        sequence = 210;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.clients.cidr;
            port = "";
          };
          destination = {
            net = config.homelab.networks.load-balancers.cidr;
            port = "";
          };
        };
      };
      clients-allow-bus-display-mqtt = {
        description = "Allow bus display to MQTT broker";
        enabled = true;
        sequence = 209;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = false;
          source = {
            net = "${config.homelab.devices.bus_display.address}/32";
            port = "";
          };
          destination = {
            net = "${fleetInventory.hosts.adguard-netbird-01.address}/32";
            port = "1883";
          };
        };
      };
      clients-allow-nas-smb = {
        description = "Allow clients to NAS Samba SMB network file sharing";
        enabled = true;
        sequence = 215;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.clients.cidr;
            port = "";
          };
          destination = {
            net = "${fleetInventory.hosts.nas-01.address}/32";
            port = "smb_ports";
          };
        };
      };
      clients-allow-spotify-connect = {
        description = "Allow clients to Music Assistant Spotify Connect players";
        enabled = true;
        sequence = 216;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.clients.cidr;
            port = "";
          };
          destination = {
            net = fleetInventory.hosts.homelab-05.address;
            port = "spotify_connect_ports";
          };
        };
      };
      clients-allow-music-assistant = {
        description = "Allow clients to Music Assistant party view";
        enabled = true;
        sequence = 217;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.clients.cidr;
            port = "";
          };
          destination = {
            net = fleetInventory.hosts.homelab-05.address;
            port = "8095";
          };
        };
      };
      clients-allow-ledfx = {
        description = "Allow clients to LedFx web interface";
        enabled = true;
        sequence = 218;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.clients.cidr;
            port = "";
          };
          destination = {
            net = fleetInventory.hosts.homelab-05.address;
            port = "8888";
          };
        };
      };
      clients-allow-mdns = {
        description = "Allow client mDNS queries to the relay";
        enabled = true;
        sequence = 219;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "UDP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.clients.cidr;
            port = "5353";
          };
          destination = {
            net = "224.0.0.251/32";
            port = "5353";
          };
        };
      };
      clients-allow-netbird-3232 = {
        description = "Allow clients host to NetBird appliance service on TCP 3232";
        enabled = true;
        sequence = 220;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = false;
          source = {
            net = "10.0.20.111/32";
            port = "";
          };
          destination = {
            net = "10.0.60.2/32";
            port = "3232";
          };
        };
      };
      clients-block-private = {
        description = "Block clients from other private VLANs";
        enabled = true;
        sequence = 221;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "block";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = true;
          source = {
            net = config.homelab.networks.clients.cidr;
            port = "";
          };
          destination = {
            net = "10.0.0.0/8";
            port = "";
          };
        };
      };
      clients-allow-internet = {
        description = "Allow clients to the Internet";
        enabled = true;
        sequence = 230;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.clients.cidr;
            port = "";
          };
          destination = {
            net = "any";
            port = "";
          };
        };
      };
      clients-block-roku-bulbs-internet = {
        description = "Block LAN-only Roku lamps from the Internet (freeze firmware)";
        enabled = true;
        sequence = 225;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "block";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = true;
          source = {
            net = "roku_lan_bulbs";
            port = "";
          };
          destination = {
            net = "any";
            port = "";
          };
        };
      };
      clients-block-bambu-internet = {
        description = "Block Bambu Lab A1 printer from the Internet (LAN mode only)";
        enabled = true;
        sequence = 227;
        interface = {
          interface = [ "opt1" ];
        };
        filter = {
          action = "block";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = true;
          source = {
            net = "iot_bambu_a1";
            port = "";
          };
          destination = {
            net = "any";
            port = "";
          };
        };
      };
      infrastructure-allow-dns = {
        description = "Allow infrastructure to OPNsense DNS";
        enabled = true;
        sequence = 300;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP/UDP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.infrastructure.cidr;
            port = "";
          };
          destination = {
            net = config.homelab.networks.infrastructure.gateway;
            port = "53";
          };
        };
      };
      infrastructure-allow-opnsense-api = {
        description = "Allow the private runner to reach the OPNsense API";
        enabled = true;
        sequence = 305;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.infrastructure.cidr;
            port = "";
          };
          destination = {
            net = config.homelab.networks.management.gateway;
            port = "443";
          };
        };
      };
      infrastructure-allow-opnsense-acl-ssh = {
        description = "Allow the CI deployment host to reconcile the OPNsense agent ACL";
        enabled = true;
        sequence = 306;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = true;
          source = {
            net = "${fleetInventory.hosts.homelab-04.address}/32";
            port = "";
          };
          destination = {
            net = "192.168.1.1/32";
            port = "22";
          };
        };
      };
      infrastructure-allow-load-balancers = {
        description = "Allow infrastructure to Kubernetes service VIPs";
        enabled = true;
        sequence = 310;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.infrastructure.cidr;
            port = "";
          };
          destination = {
            net = config.homelab.networks.load-balancers.cidr;
            port = "";
          };
        };
      };
      infrastructure-allow-bgp = {
        description = "Allow infrastructure BGP to OPNsense";
        enabled = true;
        sequence = 311;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = true;
          source = {
            net = config.homelab.networks.infrastructure.cidr;
            port = "";
          };
          destination = {
            net = config.homelab.networks.infrastructure.gateway;
            port = "179";
          };
        };
      };
      infrastructure-allow-sunshine-clients = {
        description = "Allow homelab-04 and homelab-05 to reach Sunshine streaming host on clients VLAN";
        enabled = true;
        sequence = 313;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP/UDP";
          quick = true;
          log = false;
          source = {
            net = "moonlight_clients";
            port = "";
          };
          destination = {
            net = config.homelab.networks.clients.cidr;
            port = "sunshine_ports";
          };
        };
      };
      infrastructure-allow-roku-bulbs = {
        description = "Allow roku-bridge to Roku bulbs local API";
        enabled = true;
        sequence = 314;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = true;
          source = {
            net = "${fleetInventory.hosts.adguard-netbird-01.address}/32";
            port = "";
          };
          destination = {
            net = "roku_bulbs_lo";
            port = "88";
          };
        };
      };
      infrastructure-allow-mdns-relay = {
        description = "Allow relayed client mDNS queries into infrastructure VLAN";
        enabled = true;
        sequence = 309;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "UDP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.clients.cidr;
            port = "5353";
          };
          destination = {
            net = "224.0.0.251/32";
            port = "5353";
          };
        };
      };
      infrastructure-allow-matter-bulbs = {
        description = "Allow Home Assistant Matter traffic to clients VLAN";
        enabled = true;
        sequence = 318;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet6";
          protocol = "TCP/UDP";
          quick = true;
          log = true;
          source = {
            net = "fd42:30::/64";
            port = "";
          };
          destination = {
            net = "fd42:20::/64";
            port = "";
          };
        };
      };
      infrastructure-allow-bambu-lan = {
        description = "Allow infrastructure to Bambu Lab A1 LAN mode services";
        enabled = true;
        sequence = 315;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.infrastructure.cidr;
            port = "";
          };
          destination = {
            net = "iot_bambu_a1";
            port = "iot_bambu_ports";
          };
        };
      };
      infrastructure-allow-tuya-local = {
        description = "Allow infrastructure to Feit switch Tuya LAN protocol";
        enabled = true;
        sequence = 316;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.infrastructure.cidr;
            port = "";
          };
          destination = {
            net = "iot_tuya_sw01";
            port = "6668";
          };
        };
      };
      infrastructure-allow-tuya-local-sw02 = {
        description = "Allow infrastructure to second Tuya switch LAN protocol";
        enabled = true;
        sequence = 317;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.infrastructure.cidr;
            port = "";
          };
          destination = {
            net = "iot_tuya_sw02";
            port = "6668";
          };
        };
      };
      infrastructure-allow-spotify-matrix = {
        description = "Allow Kubernetes nodes to Pico W matrix TCP receiver";
        enabled = true;
        sequence = 319;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = false;
          source = {
            net = "spotify_matrix_senders";
            port = "";
          };
          destination = {
            net = "${config.homelab.devices.spotify_matrix.address}/32";
            port = "9999";
          };
        };
      };
      infrastructure-block-private = {
        description = "Block infrastructure from initiating to other private VLANs";
        enabled = true;
        sequence = 320;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "block";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = true;
          source = {
            net = config.homelab.networks.infrastructure.cidr;
            port = "";
          };
          destination = {
            net = "10.0.0.0/8";
            port = "";
          };
        };
      };
      infrastructure-allow-internet = {
        description = "Allow infrastructure to the Internet";
        enabled = true;
        sequence = 330;
        interface = {
          interface = [ "opt3" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.infrastructure.cidr;
            port = "";
          };
          destination = {
            net = "any";
            port = "";
          };
        };
      };
      load-balancers-allow-any = {
        description = "Allow Kubernetes service VIP traffic";
        enabled = true;
        sequence = 400;
        interface = {
          interface = [ "opt4" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.load-balancers.cidr;
            port = "";
          };
          destination = {
            net = "any";
            port = "";
          };
        };
      };
      guest-iot-allow-dns = {
        description = "Allow guest and IoT devices to OPNsense DNS";
        enabled = true;
        sequence = 500;
        interface = {
          interface = [ "opt5" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP/UDP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.guest-iot.cidr;
            port = "";
          };
          destination = {
            net = config.homelab.networks.guest-iot.gateway;
            port = "53";
          };
        };
      };
      guest-iot-allow-music-assistant = {
        description = "Allow guest and IoT devices to Music Assistant party view";
        enabled = true;
        sequence = 505;
        interface = {
          interface = [ "opt5" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.guest-iot.cidr;
            port = "";
          };
          destination = {
            net = fleetInventory.hosts.homelab-05.address;
            port = "8095";
          };
        };
      };
      guest-iot-allow-ledfx = {
        description = "Allow guest and IoT devices to LedFx web interface";
        enabled = true;
        sequence = 506;
        interface = {
          interface = [ "opt5" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "TCP";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.guest-iot.cidr;
            port = "";
          };
          destination = {
            net = fleetInventory.hosts.homelab-05.address;
            port = "8888";
          };
        };
      };
      guest-iot-block-private = {
        description = "Block guest and IoT devices from private VLANs";
        enabled = true;
        sequence = 510;
        interface = {
          interface = [ "opt5" ];
        };
        filter = {
          action = "block";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = true;
          source = {
            net = config.homelab.networks.guest-iot.cidr;
            port = "";
          };
          destination = {
            net = "10.0.0.0/8";
            port = "";
          };
        };
      };
      guest-iot-allow-internet = {
        description = "Allow guest and IoT devices to the Internet";
        enabled = true;
        sequence = 520;
        interface = {
          interface = [ "opt5" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = false;
          source = {
            net = config.homelab.networks.guest-iot.cidr;
            port = "";
          };
          destination = {
            net = "any";
            port = "";
          };
        };
      };
      netbird-allow-infrastructure = {
        description = "Allow NetBird peers to permitted homelab destinations";
        enabled = true;
        sequence = 600;
        interface = {
          interface = [ "opt6" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "any";
          quick = true;
          log = true;
          source = {
            net = config.homelab.networks.netbird.cidr;
            port = "";
          };
          destination = {
            net = "netbird_allowed_dests";
            port = "";
          };
        };
      };
      wan-allow-netbird-wireguard = {
        description = "Allow forwarded NetBird WireGuard to adguard netbird 01";
        enabled = true;
        sequence = 700;
        interface = {
          interface = [ "wan" ];
        };
        filter = {
          action = "pass";
          direction = "in";
          ip_protocol = "inet";
          protocol = "UDP";
          quick = true;
          log = false;
          source = {
            net = "any";
            port = "";
          };
          destination = {
            net = "${fleetInventory.hosts.adguard-netbird-01.address}/32";
            port = "51820";
          };
        };
      };
    };
    portForwards = {
      wan-monero-p2p = {
        description = "WAN Monero P2P to homelab 04";
        enabled = true;
        sequence = 100;
        interface = [ "wan" ];
        ip_protocol = "inet";
        protocol = "tcp";
        log = false;
        nat_reflection = "default";
        source = {
          net = "any";
        };
        destination = {
          net = "wanip";
          port = "18080";
        };
        target = {
          ip = fleetInventory.hosts.homelab-04.address;
          port = "18080";
        };
      };
      wan-netbird-wireguard = {
        description = "WAN NetBird WireGuard to adguard netbird 01";
        enabled = true;
        sequence = 110;
        interface = [ "wan" ];
        ip_protocol = "inet";
        protocol = "udp";
        log = false;
        nat_reflection = "default";
        source = {
          net = "any";
        };
        destination = {
          net = "wanip";
          port = "51820";
        };
        target = {
          ip = fleetInventory.hosts.adguard-netbird-01.address;
          port = "51820";
        };
      };
    };
  };
}
