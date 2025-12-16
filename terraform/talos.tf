resource "proxmox_virtual_environment_vm" "talos_control_01" {
  name      = "Talos-Control-01"
  node_name = var.node_name
  vm_id     = 300
  started   = true
  on_boot   = true

  agent {
    enabled = false
  }

  cpu {
    cores = 2
    type  = "host"
  }

  memory {
    dedicated = 8192
  }

  disk {
    datastore_id = "local-lvm"
    interface    = "scsi0"
    file_format  = "raw"
    size         = 40
    ssd          = true
    discard      = "on"
  }

  network_device {
    bridge = "vmbr0"
    model  = "virtio"
  }

  cdrom {
    file_id = "local:iso/metal-amd64.iso"
  }
}