locals {
  subnet_map = {
    lan     = opnsense_kea_subnet.lan.id
    cluster = opnsense_kea_subnet.cluster.id
  }

  reservations = {
    hp_mini = {
      subnet = "lan", ip = "192.168.1.10", mac = "18:60:24:28:7d:1a", desc = "HP Mini"
    }
    chromebook = {
      subnet = "lan", ip = "192.168.1.70", mac = "0c:37:96:b2:a6:1f", desc = "Chromebook"
    }
    tp_switch = {
      subnet = "lan", ip = "192.168.1.2", mac = "bc:07:1d:2e:79:82", desc = "TP Link Switch"
    }
    netbird_lxc = {
      subnet = "lan", ip = var.netbird_lxc_ip_addr, mac = var.netbird_lxc_mac_address, desc = "Netbird LXC"
    }
    adguard_lxc = {
      subnet = "lan", ip = var.adguard_lxc_ip_addr, mac = var.adguard_lxc_mac_address, desc = "AdGuard LXC"
    }
    talos_cp_01 = {
      subnet = "cluster", ip = var.talos_cp_01_ip_addr, mac = var.talos_vm_mac_address, desc = "Talos Control Plane 1"
    }
    talos_worker_01 = {
      subnet = "cluster", ip = var.talos_worker_01_ip_addr, mac = var.cli01_mac_address, desc = "Talos Worker 1"
    }
    talos_worker_02 = {
      subnet = "cluster", ip = var.talos_worker_02_ip_addr, mac = var.cli02_mac_address, desc = "Talos Worker 2"
    }
    talos_worker_03 = {
      subnet = "cluster", ip = var.talos_worker_03_ip_addr, mac = var.wyse_mac_address, desc = "Talos Worker 3"
    }
  }
}

resource "opnsense_kea_subnet" "lan" {
  subnet          = "192.168.1.0/24"
  match_client_id = false
  pools           = ["192.168.1.100-192.168.1.254"]
  description     = "LAN"
}

resource "opnsense_kea_subnet" "cluster" {
  subnet          = "10.0.20.0/24"
  match_client_id = false
  pools           = ["10.0.20.100-10.0.20.179"]
  routers         = ["10.0.20.1"]
  dns_servers     = ["10.0.20.1", "8.8.8.8", "1.1.1.1"]
  description     = "CLUSTER"
}

resource "opnsense_kea_reservation" "this" {
  for_each = local.reservations

  subnet_id   = local.subnet_map[each.value.subnet]
  ip_address  = each.value.ip
  mac_address = each.value.mac
  description = each.value.desc
}