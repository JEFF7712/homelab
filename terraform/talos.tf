resource "proxmox_virtual_environment_vm" "talos_cp_01" {
  name      = "Talos-Control-01"
  node_name = var.node_name
  vm_id     = 300
  started   = true
  on_boot   = true

  cpu {
    cores = 2
    type  = "host"
  }

  memory {
    dedicated = 8192
  }

  disk {
    datastore_id = "nvme-pool"
    interface    = "scsi0"
    file_format  = "raw"
    size         = 40
    ssd          = true
    discard      = "on"
  }

  network_device {
    bridge = "vmbr0"
    model  = "virtio"
    mac_address = "BC:24:11:00:00:50"
  }

  cdrom {
    file_id = "local:iso/metal-amd64.iso"
  }
}