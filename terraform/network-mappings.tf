resource "opnsense_kea_subnet" "lan" {
  subnet = "192.168.1.1/24"
  match_client_id = false
    pools = [
    "192.168.1.1-192.168.1.254"
  ]
  description = "LAN"
}

resource "opnsense_kea_reservation" "hp_mini" {
  subnet_id    = opnsense_kea_subnet.lan.id
  ip_address   = "192.168.1.10"
  mac_address  = "18:60:24:28:7d:1a"
  description  = "HP Mini"
}

resource "opnsense_kea_reservation" "chromebook" {
  subnet_id    = opnsense_kea_subnet.lan.id
  ip_address   = "192.168.1.70"
  mac_address  = "0c:37:96:b2:a6:1f"
  description  = "Chromebook"
}

resource "opnsense_kea_reservation" "tp_switch" {
  subnet_id    = opnsense_kea_subnet.lan.id
  ip_address   = "192.168.1.2"
  mac_address  = "bc:07:1d:2e:79:82"
  description  = "TP Link Switch"
}

resource "opnsense_kea_reservation" "netbird_lxc" {
  subnet_id    = opnsense_kea_subnet.lan.id
  ip_address   = "192.168.1.5"
  mac_address  = "BC:24:11:11:EF:2F"
  description  = "Netbird LXC"
}

resource "opnsense_kea_reservation" "talos_cp_01" {
  subnet_id    = opnsense_kea_subnet.lan.id
  ip_address   = "192.168.1.101"
  mac_address  = "BC:24:11:00:00:50"
  description  = "Talos Control Plane 1"
}

resource "opnsense_kea_reservation" "talos_cp_02" {
  subnet_id    = opnsense_kea_subnet.lan.id
  ip_address   = "192.168.1.102"
  mac_address  = "00:50:ac:93:01:b0"
  description  = "Talos Control Plane 2"
}

resource "opnsense_kea_reservation" "talos_cp_03" {
  subnet_id    = opnsense_kea_subnet.lan.id
  ip_address   = "192.168.1.103"
  mac_address  = "00:50:ac:93:02:d6"
  description  = "Talos Control Plane 3"
}