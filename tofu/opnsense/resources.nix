{ inventory, lib, ... }:
let
  cfg = inventory;
  dhcpNetworks = lib.filterAttrs (_: network: network.dhcp != null) cfg.networks;
  managed =
    name: fields:
    {
      for_each = "\${local.${name}}";
    }
    // lib.genAttrs fields (field: "\${each.value.${field}}");
  endpoint = name: {
    net = "\${each.value.${name}.net}";
    port = "\${each.value.${name}.port}";
  };
in
{
  locals = {
    vlans = lib.mapAttrs (_: network: { inherit (network) description parent tag; }) cfg.networks;
    dhcpv4_subnets = lib.mapAttrs (_: network: {
      inherit (network) description;
      inherit (network.dhcp) dns_servers pools;
      routers = [ network.gateway ];
      subnet = network.cidr;
    }) dhcpNetworks;
    dhcpv4_reservations = lib.mapAttrs (_: device: {
      inherit (device) description hostname;
      ip_address = device.address;
      mac_address = device.mac;
      subnet_id = cfg.networks.${device.network}.dhcp.subnetId;
    }) cfg.devices;
    firewall_aliases = lib.mapAttrs (name: alias: {
      inherit name;
      inherit (alias) description type enabled;
      content =
        if alias.devices == [ ] then
          alias.content
        else
          map (device: cfg.devices.${device}.address) alias.devices;
    }) cfg.aliases;
    firewall_filters = cfg.rules;
    firewall_nat_port_forwards = cfg.portForwards;
  };
  resource = {
    opnsense_interfaces_vlan.managed =
      managed "vlans" [
        "description"
        "parent"
        "tag"
      ]
      // {
        lifecycle.ignore_changes = [ "device" ];
      };
    opnsense_kea_dhcpv4_subnet.managed =
      managed "dhcpv4_subnets" [
        "description"
        "dns_servers"
        "pools"
        "routers"
        "subnet"
      ]
      // {
        auto_collect = false;
      };
    opnsense_kea_dhcpv4_reservation.managed = managed "dhcpv4_reservations" [
      "description"
      "hostname"
      "ip_address"
      "mac_address"
      "subnet_id"
    ];
    opnsense_firewall_alias.managed = managed "firewall_aliases" [
      "name"
      "description"
      "type"
      "enabled"
      "content"
    ];
    opnsense_firewall_filter.managed =
      managed "firewall_filters" [
        "description"
        "enabled"
        "sequence"
      ]
      // {
        depends_on = [ "opnsense_firewall_alias.managed" ];
        interface.interface = "\${each.value.interface.interface}";
        filter =
          lib.genAttrs [ "action" "direction" "ip_protocol" "protocol" "quick" "log" ] (
            field: "\${each.value.filter.${field}}"
          )
          // {
            source = endpoint "filter.source";
            destination = endpoint "filter.destination";
          };
      };
    opnsense_firewall_nat_port_forward.managed =
      managed "firewall_nat_port_forwards" [
        "description"
        "enabled"
        "sequence"
        "interface"
        "ip_protocol"
        "protocol"
        "log"
        "nat_reflection"
      ]
      // {
        source = endpoint "source";
        destination = endpoint "destination";
        target = {
          ip = "\${each.value.target.ip}";
          port = "\${each.value.target.port}";
        };
      };
  };
}
