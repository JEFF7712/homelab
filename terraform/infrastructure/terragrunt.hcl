include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  extra_arguments "secrets" {
    commands = ["plan", "apply", "destroy", "import", "push", "refresh"]
    arguments = [
      "-var-file=${get_parent_terragrunt_dir()}/secrets.tfvars"
    ]
  }
}

inputs = {
  cluster_name     = "homelab"
  cluster_endpoint = "https://10.0.20.101:6443"
}
