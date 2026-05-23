# resource "proxmox_virtual_environment_vm" "openclaw" {
#   name      = "openclaw"
#   node_name = var.node_name
#   vm_id     = 500
#   started   = true
#   on_boot   = true

#   clone {
#     vm_id = 9000
#     full  = true
#   }

#   initialization {
#     datastore_id = "local"
#     user_account {
#       username = "claw"
#       password = var.password
#       keys     = [file("~/.ssh/id_ed25519.pub")]
#     }

#     ip_config {
#       ipv4 {
#         address = "${var.openclaw_vm_ip_addr}/24"
#         gateway = var.default_gateway
#       }
#     }

#     dns {
#       servers = ["1.1.1.1", "8.8.8.8"]
#     }
#   }

#   cpu {
#     cores = 2
#     type  = "host"
#   }

#   memory {
#     dedicated = 4096
#     floating  = 4096
#   }

#   disk {
#     datastore_id = "local"
#     interface    = "scsi0" 
#     size         = 65
#     file_format  = "raw"
#     ssd          = true
#     discard      = "on"
#     iothread     = true
#   }

#   network_device {
#     bridge = "vmbr0" 
#     model  = "virtio"
#     mac_address = var.openclaw_vm_mac_address
#   }

#   operating_system {
#     type = "l26" 
#   }

#   agent {
#     enabled = true
#     trim    = true
#   }

#   lifecycle {
#     ignore_changes = [
#       disk[0].size,
#       disk[0].file_format,
#       initialization
#     ]
#   }
# }