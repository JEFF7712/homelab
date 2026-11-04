{ lib, ... }:
let
  mode = "tplinkeasysmart_vlan_mode.switch";
  common = {
    lifecycle.prevent_destroy = true;
    depends_on = [ mode ];
  };
  fields = names: lib.genAttrs names (name: "\${each.value.${name}}");
in
{
  variable = {
    switch_name = {
      type = "string";
      description = "The switch whose independent state is being managed.";
      validation = [
        {
          condition = "\${contains([\"upstairs\", \"downstairs\"], var.switch_name)}";
          error_message = "Select upstairs or downstairs.";
        }
      ];
    };
    adopt_existing = {
      type = "bool";
      default = false;
      description = "Import existing objects; the runner permits only no-op import plans.";
    };
  };
  locals = {
    inventory = "\${jsondecode(file(\"\${path.module}/inventory.json\"))}";
    switch = "\${local.inventory.switches[var.switch_name]}";
  };
  provider.tplinkeasysmart.default = {
    host = "\${local.switch.host}";
    mac = "\${local.switch.mac}";
  };
  data.tplinkeasysmart_system_info.switch = { };
  resource = {
    tplinkeasysmart_vlan_mode.switch = {
      mode = "802.1q";
      lifecycle = {
        prevent_destroy = true;
        # Tofunix 3 calls these preconditions; the adapter emits precondition.
        preconditions = [
          {
            condition = "\${data.tplinkeasysmart_system_info.switch.mac_address == local.switch.mac && data.tplinkeasysmart_system_info.switch.ip_address == local.switch.host && data.tplinkeasysmart_system_info.switch.model == local.switch.model && data.tplinkeasysmart_system_info.switch.hardware_version == local.switch.hardware && data.tplinkeasysmart_system_info.switch.firmware_version == local.switch.firmware}";
            error_message = "Switch identity or firmware differs from the adopted inventory.";
          }
        ];
      };
    };
    tplinkeasysmart_system.description = common // {
      count = "\${local.switch.description == null ? 0 : 1}";
      hostname = "\${coalesce(local.switch.description, \"unused\")}";
    };
    tplinkeasysmart_port.ports =
      common
      // fields [
        "enabled"
        "speed"
        "flow_control"
      ]
      // {
        for_each = "\${local.switch.ports}";
        port = "\${tonumber(each.key)}";
        allow_disruptive = false;
      };
    tplinkeasysmart_vlan_8021q_group.vlans =
      common
      // fields [
        "name"
        "tagged_ports"
        "untagged_ports"
      ]
      // {
        for_each = "\${local.switch.vlans}";
        vlan = "\${tonumber(each.key)}";
        allow_disruptive = false;
      };
    tplinkeasysmart_vlan_8021q_pvid.pvids = common // {
      for_each = "\${local.switch.ports}";
      port = "\${tonumber(each.key)}";
      pvid = "\${each.value.pvid}";
      allow_disruptive = false;
      depends_on = [ "tplinkeasysmart_vlan_8021q_group.vlans" ];
    };
  };
  output.switch.value = {
    name = "\${var.switch_name}";
    model = "\${data.tplinkeasysmart_system_info.switch.model}";
    mac = "\${data.tplinkeasysmart_system_info.switch.mac_address}";
    firmware = "\${data.tplinkeasysmart_system_info.switch.firmware_version}";
    ports = "\${data.tplinkeasysmart_system_info.switch.port_count}";
    vlans = "\${length(tplinkeasysmart_vlan_8021q_group.vlans)}";
  };
}
