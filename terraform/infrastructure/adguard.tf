# resource "proxmox_virtual_environment_container" "adguard" {
#   description = "AdGuard Home DNS"
#   node_name   = var.node_name
#   vm_id       = 400
#   started     = false

#   cpu {
#     cores = 1
#   }

#   memory {
#     dedicated = 512
#     swap     = 512
#   }

#   initialization {
#     hostname = "adguard-lxc"

#     user_account {
#       password = var.password
#       keys     = [file("~/.ssh/id_ed25519.pub")]
#     }

#     ip_config {
#       ipv4 {
#         address = "${var.adguard_lxc_ip_addr}/24"
#         gateway = var.default_gateway
#       }
#     }
#   }

#   disk {
#     datastore_id = "local"
#     size         = 8
#   }  

#   network_interface {
#     name   = "eth0"
#     bridge = "vmbr0"
#     mac_address = var.adguard_lxc_mac_address
#   }

#   operating_system {
#     template_file_id = "local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst"
#     type             = "debian"
#   }

#   unprivileged = true

#   features {
#     nesting = true
#   }
# }