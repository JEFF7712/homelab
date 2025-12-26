locals {
  node_data = {
    controlplanes = {
      (var.talos_cp_01_ip_addr) = {
        install_disk = "/dev/sda"
        hostname     = "controlplane-01-vm"
      }
    }
    # workers = {
    #   (var.talos_worker_01_ip_addr) = {
    #     install_disk = "/dev/sdb"
    #     hostname     = "worker-01-cli"
    #   },
    #   (var.talos_worker_02_ip_addr) = {
    #     install_disk = "/dev/sdb"
    #     hostname     = "worker-02-cli"
    #   },
    #    (var.talos_worker_03_ip_addr) = {
    #      install_disk = "/dev/sda"
    #      hostname     = "worker-03-wyse"
    #    }
    # }
  }
}

resource "talos_machine_secrets" "this" {
    talos_version = "v1.12.0"
}

data "talos_client_configuration" "this" {
  cluster_name         = var.cluster_name
  client_configuration = talos_machine_secrets.this.client_configuration
  endpoints            = [for k, v in local.node_data.controlplanes : k]
}

data "talos_machine_configuration" "controlplane" {
  cluster_name         = var.cluster_name
  cluster_endpoint     = var.cluster_endpoint
  machine_type         = "controlplane"
  talos_version        = talos_machine_secrets.this.talos_version
  kubernetes_version   = "v1.35.0"
  machine_secrets      = talos_machine_secrets.this.machine_secrets
}

# data "talos_machine_configuration" "worker" {
#   cluster_name         = var.cluster_name
#   cluster_endpoint     = var.cluster_endpoint
#   machine_type         = "worker"
#   talos_version        = talos_machine_secrets.this.talos_version
#   kubernetes_version   = "v1.34.0"
#   machine_secrets      = talos_machine_secrets.this.machine_secrets
# }

resource "proxmox_virtual_environment_vm" "controlplane_01" {
  name      = "Talos-Controlplane-1"
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
    vlan_id = 20
  }

  cdrom {
    file_id = "local:iso/metal-amd64.iso"
  }
}

resource "talos_machine_configuration_apply" "controlplane" {
  depends_on           = [ proxmox_virtual_environment_vm.controlplane_01 ]
  client_configuration = talos_machine_secrets.this.client_configuration
  machine_configuration_input = yamlencode({
    for key, value in yamldecode(split("\n---", data.talos_machine_configuration.controlplane.machine_configuration)[0]) : 
    key => value if key != "hostname"
  })
  for_each             = local.node_data.controlplanes
  node                 = each.key

  config_patches = [
    templatefile("${path.module}/templates/install-disk-and-hostname.yaml.tmpl", {
      hostname     = each.value.hostname == null ? format("%s-cp-%s", var.cluster_name, index(keys(local.node_data.controlplanes), each.key)) : each.value.hostname
      install_disk = each.value.install_disk
    }),
    file("${path.module}/files/cp-scheduling.yaml"),
    yamlencode({
    machine = {
      features = {
        kubePrism = {
          enabled = true
          port    = 7445
        }
      }
    }
    cluster = {
      network = {
        cni = { name = "none" }
      }
      proxy = {
        disabled = true
      }
    }
  })
  ]
}

# resource "talos_machine_configuration_apply" "worker" {
#   client_configuration        = talos_machine_secrets.this.client_configuration
#   machine_configuration_input = data.talos_machine_configuration.worker.machine_configuration
#   for_each                    = local.node_data.workers
#   node                        = each.key
#   config_patches = [
#     templatefile("${path.module}/templates/install-disk-and-hostname.yaml.tmpl", {
#       hostname     = each.value.hostname == null ? format("%s-worker-%s", var.cluster_name, index(keys(local.node_data.workers), each.key)) : each.value.hostname
#       install_disk = each.value.install_disk
#     })
#   ]
# }

resource "talos_machine_bootstrap" "this" {
  depends_on = [talos_machine_configuration_apply.controlplane]
  client_configuration = talos_machine_secrets.this.client_configuration
  node                 = [for k, v in local.node_data.controlplanes : k][0]
}

resource "talos_cluster_kubeconfig" "this" {
  depends_on           = [talos_machine_bootstrap.this]
  client_configuration = talos_machine_secrets.this.client_configuration
  node                 = [for k, v in local.node_data.controlplanes : k][0]
}