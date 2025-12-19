terraform {
  required_providers {
    proxmox = {
      source = "bpg/proxmox"
      version = "0.89.1"
    }
    talos = {
      source = "siderolabs/talos"
      version = "0.10.0-beta.0"
    }
    opnsense = {
      source = "browningluke/opnsense"
      version = "0.16.1"
    }
    flux = {
      source  = "fluxcd/flux"
      version = ">= 1.2"
    }
    gitlab = {
      source  = "gitlabhq/gitlab"
      version = ">= 16.10"
    }
    tls = {
      source  = "hashicorp/tls"
      version = ">= 4.0"
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

provider "flux" {
kubernetes = {
    host                   = local.kube_host
    client_certificate     = local.kube_client_cert
    client_key             = local.kube_client_key
    cluster_ca_certificate = local.kube_cluster_ca
  }
  git = {
    url = "ssh://git@gitlab.com/${data.gitlab_project.this.path_with_namespace}.git"
    ssh = {
      username    = "git"
      private_key = tls_private_key.flux.private_key_pem
    }
  }
}

locals {
  kube_config = yamldecode(resource.talos_cluster_kubeconfig.this.kubeconfig_raw)
  kube_host        = local.kube_config.clusters[0].cluster.server
  kube_cluster_ca  = base64decode(local.kube_config.clusters[0].cluster["certificate-authority-data"])
  kube_client_cert = base64decode(local.kube_config.users[0].user["client-certificate-data"])
  kube_client_key  = base64decode(local.kube_config.users[0].user["client-key-data"])
}