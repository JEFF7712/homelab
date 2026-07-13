resource "proxmox_virtual_environment_container" "netbird" {
  description = "NetBird VPN Gateway"
  node_name   = var.node_name
  vm_id       = 200
  started     = true

  cpu {
    cores = 1
  }

  memory {
    dedicated = 512
    swap      = 512
  }

  initialization {
    hostname = "netbird-lxc"

    user_account {
      password = var.password
      keys     = compact([local.ssh_public_key])
    }

    ip_config {
      ipv4 {
        address = "${var.netbird_lxc_ip_addr}/24"
        gateway = var.default_gateway
      }
    }

    ip_config {
      ipv4 {
        address = "${var.netbird_lxc_ip_addr2}/24"
      }
    }
  }

  disk {
    datastore_id = "local"
    size         = 8
  }

  network_interface {
    name        = "eth0"
    bridge      = "vmbr0"
    mac_address = var.netbird_lxc_mac_address
  }


  network_interface {
    name        = "eth1"
    bridge      = "vmbr0"
    vlan_id     = 20
    mac_address = var.netbird_lxc_mac_address2
  }

  operating_system {
    template_file_id = "local:vztmpl/alpine-3.22-default_20250617_amd64.tar.xz"
    type             = "alpine"
  }

  unprivileged = true

  features {
    nesting = true
  }

  device_passthrough {
    path = "/dev/net/tun"
    mode = "0666"
  }
}

# Keep the legacy snippet present until the include is removed from the live LXC config.
moved {
  from = proxmox_virtual_environment_file.netbird_lxc_config
  to   = proxmox_virtual_environment_file.netbird_lxc_legacy_config
}

resource "proxmox_virtual_environment_file" "netbird_lxc_legacy_config" {
  content_type = "snippets"
  datastore_id = "local"
  node_name    = var.node_name

  source_raw {
    data = <<-EOF
      lxc.cgroup2.devices.allow = c 10:200 rwm
      lxc.mount.entry = /dev/net dev/net none bind,create=dir
      lxc.mount.entry = /dev/net/tun dev/net/tun none bind,create=file
    EOF

    file_name = "netbird-tun-${proxmox_virtual_environment_container.netbird.vm_id}.conf"
  }
}

resource "terraform_data" "remove_legacy_netbird_include" {
  depends_on = [
    proxmox_virtual_environment_container.netbird,
    proxmox_virtual_environment_file.netbird_lxc_legacy_config,
  ]

  triggers_replace = [
    proxmox_virtual_environment_container.netbird.id,
    proxmox_virtual_environment_file.netbird_lxc_legacy_config.id,
  ]

  connection {
    type        = "ssh"
    user        = "root"
    host        = var.host_ip_addr
    private_key = local.ssh_private_key
  }

  provisioner "remote-exec" {
    inline = [
      "sed -i '/lxc.include.*netbird-tun/d' /etc/pve/lxc/${proxmox_virtual_environment_container.netbird.vm_id}.conf",
    ]
  }
}
