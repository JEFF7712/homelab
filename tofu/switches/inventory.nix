# VLAN identities follow the shared routed networks. VLANs 1 and 99 are
# switch-only default and WAN transit networks, respectively.
{ networks }: {
  schema_version = 1;
  switches = {
    downstairs = {
      host = "10.0.10.3";
      mac = "98:03:8e:34:e0:0b";
      model = "TL-SG105E";
      hardware = "TL-SG105E 5.0";
      firmware = "1.0.0 Build 20250710 Rel.71066";
      ports = {
        "1" = {
          role = "ISP/modem handoff";
          endpoint = {
            node = "isp-handoff";
            interface = "ethernet";
            uncertain = false;
          };
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = 99;
        };
        "2" = {
          role = "Upstairs wall trunk";
          endpoint = {
            node = "switch-upstairs";
            interface = "port4";
            uncertain = false;
          };
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = 1;
        };
        "3" = {
          role = "Downstairs NETGEAR access point";
          endpoint = {
            node = "downstairs-ap";
            interface = "uplink";
            uncertain = false;
          };
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.clients.tag;
        };
        "4" = {
          role = "Living room NETGEAR cluster switch drop";
          endpoint = {
            node = "living-room-cluster";
            interface = "uplink";
            uncertain = false;
          };
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.infrastructure.tag;
        };
        "5" = {
          role = "Trusted-client spare";
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.clients.tag;
        };
      };
      vlans = {
        "1" = {
          name = "Default";
          tagged_ports = [ ];
          untagged_ports = [ 2 ];
        };
        "${toString networks.management.tag}" = {
          name = "";
          tagged_ports = [ 2 ];
          untagged_ports = [ ];
        };
        "${toString networks.clients.tag}" = {
          name = "";
          tagged_ports = [ 2 ];
          untagged_ports = [
            3
            5
          ];
        };
        "${toString networks.infrastructure.tag}" = {
          name = "";
          tagged_ports = [ 2 ];
          untagged_ports = [ 4 ];
        };
        "99" = {
          name = "";
          tagged_ports = [ 2 ];
          untagged_ports = [ 1 ];
        };
      };
      description = "Downstairs-TL-SG105E";
    };
    upstairs = {
      host = "10.0.10.2";
      mac = "bc:07:1d:2e:79:82";
      model = "TL-SG108E";
      hardware = "TL-SG108E 6.0";
      firmware = "1.0.0 Build 20250710 Rel.71093";
      ports = {
        "1" = {
          role = "Unmanaged cluster access switch";
          endpoint = {
            node = "cluster-access";
            interface = "uplink";
            uncertain = false;
          };
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.infrastructure.tag;
        };
        "2" = {
          role = "OPNsense WAN NIC (port order unverified)";
          endpoint = {
            node = "opnsense";
            interface = "wan-unverified";
            uncertain = true;
          };
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = 99;
        };
        "3" = {
          role = "OPNsense LAN NIC (port order unverified)";
          endpoint = {
            node = "opnsense";
            interface = "lan-unverified";
            uncertain = true;
          };
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = 1;
        };
        "4" = {
          role = "Downstairs wall trunk";
          endpoint = {
            node = "switch-downstairs";
            interface = "port2";
            uncertain = false;
          };
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = 1;
        };
        "5" = {
          role = "AdGuard and NetBird appliance";
          endpoint = {
            node = "adguard-netbird-01";
            interface = "switch-uplink";
            uncertain = false;
          };
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.infrastructure.tag;
        };
        "6" = {
          role = "NAS (nas-01)";
          endpoint = {
            node = "nas-01";
            interface = "switch-uplink";
            uncertain = false;
          };
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.infrastructure.tag;
        };
        "7" = {
          role = "Upstairs TP-Link RE505X access point";
          endpoint = {
            node = "upstairs-ap";
            interface = "uplink";
            uncertain = false;
          };
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.clients.tag;
        };
        "8" = {
          role = "Management access";
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.management.tag;
        };
      };
      vlans = {
        "1" = {
          name = "Default";
          tagged_ports = [ ];
          untagged_ports = [
            1
            2
            3
            4
            5
            6
            7
            8
          ];
        };
        "${toString networks.management.tag}" = {
          name = "";
          tagged_ports = [
            3
            4
          ];
          untagged_ports = [ 8 ];
        };
        "${toString networks.clients.tag}" = {
          name = "";
          tagged_ports = [
            3
            4
          ];
          untagged_ports = [ 7 ];
        };
        "${toString networks.infrastructure.tag}" = {
          name = "";
          tagged_ports = [
            3
            4
          ];
          untagged_ports = [
            1
            5
            6
          ];
        };
        "${toString networks.load-balancers.tag}" = {
          name = "";
          tagged_ports = [ 3 ];
          untagged_ports = [ ];
        };
        "${toString networks.guest-iot.tag}" = {
          name = "";
          tagged_ports = [ 3 ];
          untagged_ports = [ ];
        };
        "${toString networks.netbird.tag}" = {
          name = "";
          tagged_ports = [
            3
            5
          ];
          untagged_ports = [ ];
        };
        "99" = {
          name = "";
          tagged_ports = [ 4 ];
          untagged_ports = [ 2 ];
        };
      };
      description = "Upstairs-TL-SG108E";
    };
  };
}
