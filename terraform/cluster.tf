# resource "talos_machine_secrets" "this" {}

# data "talos_client_configuration" "this" {
#   cluster_name         = var.cluster_name
#   client_configuration = talos_machine_secrets.this.client_configuration
#   endpoints            = [var.talos_cp_01_ip_addr]
# }

# data "talos_machine_configuration" "machineconfig_cp" {
#   cluster_name         = var.cluster_name
#   cluster_endpoint     = "https://${var.talos_cp_01_ip_addr}:6443"
#   machine_type         = "controlplane"
#   talos_version        = talos_machine_secrets.this.talos_version
#   machine_secrets      = talos_machine_secrets.this.machine_secrets
# }

# resource "talos_machine_configuration_apply" "cp_config_apply" {
#   depends_on                  = [ proxmox_virtual_environment_vm.talos_cp_01 ]
#   client_configuration        = talos_machine_secrets.this.client_configuration
#   machine_configuration_input = data.talos_machine_configuration.machineconfig_cp.machine_configuration
#   count                       = 1
#   node                        = var.talos_cp_01_ip_addr
# }

# data "talos_machine_configuration" "machineconfig_worker" {
#   cluster_name         = var.cluster_name
#   cluster_endpoint     = "https://${var.talos_cp_01_ip_addr}:6443"
#   machine_type         = "worker"
#   talos_version        = talos_machine_secrets.this.talos_version
#   machine_secrets      = talos_machine_secrets.this.machine_secrets
# }

# resource "talos_machine_configuration_apply" "worker_config_apply" {
#   client_configuration        = talos_machine_secrets.this.client_configuration
#   machine_configuration_input = data.talos_machine_configuration.machineconfig_worker.machine_configuration
#   count                       = 1
#   node                        = var.talos_worker_01_ip_addr
# }

# resource "talos_machine_bootstrap" "this" {
#   depends_on           = [ talos_machine_configuration_apply.cp_config_apply ]
#   client_configuration = talos_machine_secrets.this.client_configuration
#   node                 = var.talos_cp_01_ip_addr
# }

# data "talos_cluster_health" "this" {
#   depends_on           = [ talos_machine_configuration_apply.cp_config_apply, talos_machine_configuration_apply.worker_config_apply ]
#   client_configuration = data.talos_client_configuration.this.client_configuration
#   control_plane_nodes  = [ var.talos_cp_01_ip_addr ]
#   worker_nodes         = [ var.talos_worker_01_ip_addr ]
#   endpoints            = data.talos_client_configuration.this.endpoints
# }

# data "talos_cluster_kubeconfig" "this" {
#   depends_on           = [ talos_machine_bootstrap.this, data.talos_cluster_health.this ]
#   client_configuration = talos_machine_secrets.this.client_configuration
#   node                 = var.talos_cp_01_ip_addr
# }

# output "talosconfig" {
#   value = data.talos_client_configuration.this.talos_config
#   sensitive = true
# }

# output "kubeconfig" {
#   value = data.talos_cluster_kubeconfig.this.kubeconfig_raw
#   sensitive = true
# }