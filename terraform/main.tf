#   resource "local_file" "ansible_inventory" {
#   content = templatefile("${path.module}/templates/inventory.tftpl", {
#     # vm_ips = proxmox_vm_qemu.node[*].default_ipv4_address
#     # grabs the first interface's IP and strips the subnet mask
#     lxc_ips = [
#       for lxc in proxmox_lxc.container : 
#       replace(lxc.network[0].ip, "/\\/[0-9]+$/", "")
#     ]
#   })
  
#   filename = "../ansible/inventory/generated_hosts.ini"
# }
