resource "opnsense_kea_subnet" "lan" {
  subnet = "192.168.1.0/24"
  match_client_id = false
    pools = [
    "192.168.1.100-192.168.1.254"
  ]
  description = "LAN"
}

resource "opnsense_kea_subnet" "cluster" {
  subnet = "10.0.20.0/24"
  match_client_id = false
    pools = [
    "10.0.20.100-10.0.20.200"
  ]
  routers = [
    "10.0.20.1"
  ]
  dns_servers = [
    "10.0.20.1",
    "8.8.8.8",
    "1.1.1.1"
  ]
  description = "CLUSTER"
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

resource "opnsense_kea_reservation" "talos_controlplane_01" {
  subnet_id    = opnsense_kea_subnet.cluster.id
  ip_address   = var.talos_cp_01_ip_addr
  mac_address  = var.talos_vm_mac_address
  description  = "Talos Control Plane 1"
}

# resource "opnsense_kea_reservation" "talos_controlplane_02" {
#   subnet_id    = opnsense_kea_subnet.cluster.id
#   ip_address   = var.talos_cp_02_ip_addr
#   mac_address  = "00:50:ac:93:01:b0"
#   description  = "Talos Control Plane 2"
# }

# resource "opnsense_kea_reservation" "talos_controlplane_03" {
#   subnet_id    = opnsense_kea_subnet.cluster.id
#   ip_address   = var.talos_cp_03_ip_addr
#   mac_address  = "00:50:ac:93:02:d6"
#   description  = "Talos Control Plane 3"
# }
resource "opnsense_kea_reservation" "talos_worker_01" {
  subnet_id    = opnsense_kea_subnet.cluster.id
  ip_address   = var.talos_worker_01_ip_addr
  mac_address  = var.cli01_mac_address
  description  = "Talos Worker 1"
}

resource "opnsense_kea_reservation" "talos_worker_02" {
  subnet_id    = opnsense_kea_subnet.cluster.id
  ip_address   = var.talos_worker_02_ip_addr
  mac_address  = var.cli02_mac_address
  description  = "Talos Worker 2"
}

resource "opnsense_kea_reservation" "talos_worker_03" {
  subnet_id    = opnsense_kea_subnet.cluster.id
  ip_address   = var.talos_worker_03_ip_addr
  mac_address  = var.wyse_mac_address
  description  = "Talos Worker 3"
}