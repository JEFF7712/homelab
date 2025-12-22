terraform {
  extra_arguments "secrets" {
    commands = ["plan", "apply", "destroy", "import", "push", "refresh"]
    arguments = [
      "-var-file=${get_parent_terragrunt_dir()}/secrets.tfvars"
    ]
  }
}

remote_state {
  backend = "local"
  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }
  config = {
    path = "${path_relative_to_include()}/terraform.tfstate"
  }
}

inputs = {
  cluster_name = "homelab"
  cluster_endpoint = "https://10.0.20.101:6443"
  gitlab_username = "JEFF7712"
  gitlab_project = "homelab"
}
