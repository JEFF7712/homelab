terraform {
  required_providers {
    proxmox = {
      source = "bpg/proxmox"
      version = "0.89.1"
    }
    gitlab = {
      source = "gitlabhq/gitlab"
      version = "18.6.1"
    }

    talos = {
      source = "siderolabs/talos"
      version = "0.10.0-beta.0"
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

provider "gitlab" {
  token = var.gitlab_token
  # base_url = var.gitlab_base_url # Uncomment and set this if using a self-managed GitLab instance
}

provider "opnsense" {
  uri = var.opnsense_uri
  api_key = var.opnsense_api_key
  api_secret = var.opnsense_api_secret
  allow_insecure = true
}