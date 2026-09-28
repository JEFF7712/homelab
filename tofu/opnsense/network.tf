resource "opnsense_interfaces_vlan" "managed" {
  for_each = var.vlans

  description = each.value.description
  parent      = each.value.parent
  tag         = each.value.tag

  lifecycle {
    ignore_changes = [device]
  }
}

resource "opnsense_kea_dhcpv4_subnet" "managed" {
  for_each = var.dhcpv4_subnets

  auto_collect = false
  description  = each.value.description
  dns_servers  = each.value.dns_servers
  pools        = each.value.pools
  routers      = each.value.routers
  subnet       = each.value.subnet
}

resource "opnsense_kea_dhcpv4_reservation" "managed" {
  for_each = var.dhcpv4_reservations

  description = each.value.description
  hostname    = each.value.hostname
  ip_address  = each.value.ip_address
  mac_address = each.value.mac_address
  subnet_id   = each.value.subnet_id
}

resource "opnsense_firewall_alias" "managed" {
  for_each = var.firewall_aliases

  name        = each.value.name
  type        = each.value.type
  content     = each.value.content
  description = each.value.description
  enabled     = each.value.enabled
}

resource "opnsense_firewall_filter" "managed" {
  for_each = var.firewall_filters

  description = each.value.description
  enabled     = each.value.enabled
  filter      = each.value.filter
  interface   = each.value.interface
  sequence    = each.value.sequence

  depends_on = [opnsense_firewall_alias.managed]
}

resource "opnsense_firewall_nat_port_forward" "managed" {
  for_each = var.firewall_nat_port_forwards

  description    = each.value.description
  enabled        = each.value.enabled
  sequence       = each.value.sequence
  interface      = each.value.interface
  ip_protocol    = each.value.ip_protocol
  protocol       = each.value.protocol
  log            = each.value.log
  nat_reflection = each.value.nat_reflection
  source         = each.value.source
  destination    = each.value.destination
  target         = each.value.target
}
