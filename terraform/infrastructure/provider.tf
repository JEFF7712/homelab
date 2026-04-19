terraform {
  required_providers {
    proxmox = {
      source = "bpg/proxmox"
      version = "0.103.0"
    }
    talos = {
      source = "siderolabs/talos"
      version = "0.10.1"
    }
    opnsense = {
      source = "browningluke/opnsense"
      version = "0.16.1"
    }
  }
}

provider "proxmox" {
  endpoint = var.proxmox_api_url
  api_token = var.proxmox_api_token
  insecure = true
  ssh {
    agent = true
    username = "root"
  }
}

provider "opnsense" {
  uri = var.opnsense_uri
  api_key = var.opnsense_api_key
  api_secret = var.opnsense_api_secret
  allow_insecure = true
}