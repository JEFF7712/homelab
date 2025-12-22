data "gitlab_project" "this" {
  path_with_namespace = "${var.gitlab_username}/${var.gitlab_project}"
}

resource "tls_private_key" "flux" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "gitlab_deploy_key" "this" {
  project  = data.gitlab_project.this.id
  title    = "Flux Cluster: ${var.cluster_name}"
  key      = tls_private_key.flux.public_key_openssh
  can_push = true
}

resource "flux_bootstrap_git" "this" {
  path = "clusters/${var.cluster_name}"
  embedded_manifests = true
}
