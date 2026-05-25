## Sensitive variables

variable "proxmox_api_url" {
  type      = string
  sensitive = true
}
variable "proxmox_api_token" {
  type      = string
  sensitive = true
}
variable "password" {
  type      = string
  sensitive = true
}
variable "ssh_private_key" {
  type      = string
  default   = null
  sensitive = true
}
variable "ssh_public_key" {
  type    = string
  default = null
}
variable "gitlab_token" {
  type      = string
  sensitive = true
}
variable "gitlab_username" {
  type = string
}
variable "gitlab_project" {
  type = string
}

## Device variables

variable "cli01_mac_address" {
  type = string
}
variable "cli02_mac_address" {
  type = string
}
variable "talos_vm_mac_address" {
  type = string
}
variable "wyse_mac_address" {
  type = string
}
variable "netbird_lxc_mac_address" {
  type = string
}
variable "netbird_lxc_mac_address2" {
  type = string
}
variable "adguard_lxc_mac_address" {
  type = string
}
variable "openclaw_vm_mac_address" {
  type = string
}

## Cluster variables

variable "cluster_name" {
  type    = string
  default = "homelab"
}
variable "cluster_endpoint" {
  type = string
}
variable "talos_cp_01_ip_addr" {
  type = string
}
variable "talos_cp_02_ip_addr" {
  type = string
}
variable "talos_cp_03_ip_addr" {
  type = string
}
variable "talos_worker_01_ip_addr" {
  type = string
}
variable "talos_worker_02_ip_addr" {
  type = string
}
variable "talos_worker_03_ip_addr" {
  type = string
}

## Network variables

variable "default_gateway" {
  type = string
}
variable "host_ip_addr" {
  type = string
}
variable "node_name" {
  type    = string
  default = "hpmini"
}
variable "netbird_lxc_ip_addr" {
  type = string
}
variable "netbird_lxc_ip_addr2" {
  type = string
}
variable "adguard_lxc_ip_addr" {
  type = string
}
variable "openclaw_vm_ip_addr" {
  type = string
}
variable "opnsense_uri" {
  type      = string
  sensitive = true
}
variable "opnsense_api_key" {
  type      = string
  sensitive = true
}
variable "opnsense_api_secret" {
  type      = string
  sensitive = true
}

variable "kubeconfig" {
  type    = string
  default = null
}
