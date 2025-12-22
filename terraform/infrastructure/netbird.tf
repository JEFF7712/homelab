resource "proxmox_virtual_environment_container" "netbird" {
  description = "NetBird VPN Gateway"
  node_name   = var.node_name
  vm_id       = 200
  started     = true

  initialization {
    hostname = "netbird-lxc"
    
    user_account {
      password = var.password
      keys     = [file("~/.ssh/id_ed25519.pub")]
    }

    ip_config {
      ipv4 {
        address = "${var.netbird_lxc_ip_addr}/24"
        gateway = var.default_gateway
      }
    }
  }

  disk {
    datastore_id = "nvme-pool"
    size         = 8
  }  

  network_interface {
    name   = "eth0"
    bridge = "vmbr0"
    mac_address = var.netbird_lxc_mac_address
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

resource "proxmox_virtual_environment_file" "netbird_lxc_config" {
  content_type = "snippets"
  datastore_id = "local"
  node_name    = var.node_name

  source_raw {
    data = <<EOF
lxc.cgroup2.devices.allow = c 10:200 rwm
lxc.mount.entry = /dev/net dev/net none bind,create=dir
lxc.mount.entry = /dev/net/tun dev/net/tun none bind,create=file
EOF

    file_name = "netbird-tun-${proxmox_virtual_environment_container.netbird.vm_id}.conf"
  }
}
resource "null_resource" "lxc_include_injector" {
  triggers = {
    container_id = proxmox_virtual_environment_container.netbird.vm_id
    snippet_id   = proxmox_virtual_environment_file.netbird_lxc_config.id
  }

  connection {
    type        = "ssh"
    user        = "root"
    host        = var.host_ip_addr
    private_key = file("~/.ssh/id_ed25519")
  }

  provisioner "remote-exec" {
    inline = [
      <<-EOT
        CONFIG_FILE="/etc/pve/lxc/${proxmox_virtual_environment_container.netbird.vm_id}.conf"
        REAL_SNIPPET_PATH="/var/lib/vz/snippets/netbird-tun-${proxmox_virtual_environment_container.netbird.vm_id}.conf"
        
        # Clean up old entries to prevent duplicates
        sed -i '/lxc.include.*netbird-tun/d' "$CONFIG_FILE"
        
        # Append the new include line
        echo "lxc.include: $REAL_SNIPPET_PATH" >> "$CONFIG_FILE"
      EOT
    ]
  }
}


resource "null_resource" "netbird_alpine_bootstrap" {
  depends_on = [null_resource.lxc_include_injector]

  triggers = {
    container_id = proxmox_virtual_environment_container.netbird.vm_id
  }

  connection {
    type        = "ssh"
    user        = "root"
    host        = var.host_ip_addr
    private_key = file("~/.ssh/id_ed25519")
  }

  provisioner "remote-exec" {
    inline = [
      "while ! pct status ${proxmox_virtual_environment_container.netbird.vm_id} | grep -q running; do echo 'Waiting for container...'; sleep 1; done",
      "pct exec ${proxmox_virtual_environment_container.netbird.vm_id} -- apk update",
      "pct exec ${proxmox_virtual_environment_container.netbird.vm_id} -- apk add openssh",
      "pct exec ${proxmox_virtual_environment_container.netbird.vm_id} -- sed -i 's/^#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config",
      "pct exec ${proxmox_virtual_environment_container.netbird.vm_id} -- rc-update add sshd default"
    ]
  }
}

resource "null_resource" "reboot_container" {
  depends_on = [null_resource.netbird_alpine_bootstrap]

  triggers = {
    injector_id  = null_resource.lxc_include_injector.id
    bootstrap_id = null_resource.netbird_alpine_bootstrap.id
  }

  connection {
    type        = "ssh"
    user        = "root"
    host        = var.host_ip_addr
    private_key = file("~/.ssh/id_ed25519")
  }

  provisioner "remote-exec" {
    inline = [
      "echo 'Restarting container to apply TUN config...'",
      "pct stop ${proxmox_virtual_environment_container.netbird.vm_id}",
      "while pct status ${proxmox_virtual_environment_container.netbird.vm_id} | grep -q running; do sleep 1; done",
      "pct start ${proxmox_virtual_environment_container.netbird.vm_id}",
      "until pct status ${proxmox_virtual_environment_container.netbird.vm_id} | grep -q running; do sleep 1; done",
      "echo 'Container restarted successfully.'"
    ]
  }
}