variable proxmox_api_url {
    type = string
    sensitive = true
}
variable proxmox_api_token {
    type = string
    sensitive = true
}
variable "node_name" {
  default = "hpmini"
}
variable "password" {
  type = string
  sensitive = true
}

variable "gitlab_token" {
  type = string
  sensitive = true
}

variable "cluster_name" {
  type    = string
  default = "homelab"
}

variable "default_gateway" {
  type    = string
}

variable "host_ip_addr" {
  type    = string
}

variable "netbird_lxc_ip_addr" {
  type    = string
}

variable "talos_cp_01_ip_addr" {
  type    = string
}

variable "talos_worker_01_ip_addr" {
  type    = string
}