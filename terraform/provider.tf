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