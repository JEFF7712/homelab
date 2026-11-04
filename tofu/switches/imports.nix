[
  {
    for_each = "\${local.switch.description == null ? {} : { description = \"system\" }}";
    to = "tplinkeasysmart_system.description[0]";
    id = "\${each.value}";
  }
  {
    for_each = "\${var.adopt_existing ? { mode = \"vlan_mode\" } : {}}";
    to = "tplinkeasysmart_vlan_mode.switch";
    id = "\${each.value}";
  }
  {
    for_each = "\${var.adopt_existing ? local.switch.ports : {}}";
    to = "tplinkeasysmart_port.ports[each.key]";
    id = "\${each.key}";
  }
  {
    for_each = "\${var.adopt_existing ? local.switch.vlans : {}}";
    to = "tplinkeasysmart_vlan_8021q_group.vlans[each.key]";
    id = "\${each.key}";
  }
  {
    for_each = "\${var.adopt_existing ? local.switch.ports : {}}";
    to = "tplinkeasysmart_vlan_8021q_pvid.pvids[each.key]";
    id = "\${each.key}";
  }
]
