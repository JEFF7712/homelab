# data "gitlab_project" "flux" {
#   path_with_namespace = "${var.gitlab_username}/${var.gitlab_project_name}"
# }

# resource "flux_bootstrap_git" "this" {
#   path = "clusters/talos-homelab"
#   embedded_manifests = true
#   components_extra   = ["image-reflector-controller", "image-automation-controller"]
# }