terraform {
  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = ">= 3.1"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 3.0"
    }
  }
}

locals {
  kubeconfig      = yamldecode(var.kubeconfig)
  current_context = one([for context in local.kubeconfig.contexts : context.context if context.name == local.kubeconfig["current-context"]])
  cluster         = one([for cluster in local.kubeconfig.clusters : cluster.cluster if cluster.name == local.current_context.cluster])
  user            = one([for user in local.kubeconfig.users : user.user if user.name == local.current_context.user])
}

provider "helm" {
  kubernetes = {
    host                   = local.cluster.server
    cluster_ca_certificate = base64decode(local.cluster["certificate-authority-data"])
    client_certificate     = base64decode(local.user["client-certificate-data"])
    client_key             = base64decode(local.user["client-key-data"])
  }
}
