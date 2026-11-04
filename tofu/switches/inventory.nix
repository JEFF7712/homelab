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
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = 99;
        };
        "2" = {
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = 1;
        };
        "3" = {
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.clients.tag;
        };
        "4" = {
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.infrastructure.tag;
        };
        "5" = {
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
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.infrastructure.tag;
        };
        "2" = {
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = 99;
        };
        "3" = {
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = 1;
        };
        "4" = {
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = 1;
        };
        "5" = {
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.infrastructure.tag;
        };
        "6" = {
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.infrastructure.tag;
        };
        "7" = {
          enabled = true;
          speed = "auto";
          flow_control = false;
          pvid = networks.clients.tag;
        };
        "8" = {
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
