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