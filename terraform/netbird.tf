resource "proxmox_virtual_environment_container" "netbird" {
  description = "NetBird VPN Gateway"
  node_name   = var.node_name
  vm_id       = 200
  started     = true

  initialization {
    hostname = "netbird-lxc"

    ip_config {
      ipv4 {
        address = "192.168.1.5/24"
        gateway = "192.168.1.1"
      }
    }
    
    user_account {
      password = var.password
    }
  }

  disk {
    datastore_id = "local-lvm"
    size         = 8
  }  

  network_interface {
    name   = "eth0"
    bridge = "vmbr0"
  }

  operating_system {
    template_file_id = "local:vztmpl/alpine-3.22-default_20250617_amd64.tar.xz"
    type             = "alpine"
  }

  unprivileged = true

  features {
    nesting = true
  }
}