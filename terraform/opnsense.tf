resource "proxmox_virtual_environment_vm" "opnsense" {
  name      = "OPNsense-Router"
  node_name = var.node_name
  vm_id     = 100
  started   = true
  on_boot   = true

  cpu {
    cores = 2
    type  = "host"
  }

  memory {
    dedicated = 4096
  }

  disk {
    datastore_id = "nvme-pool"
    interface    = "virtio0"
    size         = 32
  }

  network_device {
    bridge = "vmbr0"
    model  = "virtio"
  }

  network_device {
    bridge = "vmbr0" 
    model  = "virtio"
  }

  lifecycle {
    prevent_destroy = true  
    ignore_changes = [
      agent,                
      network_device,       
    ]
  }

  agent { enabled = false }

}