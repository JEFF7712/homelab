include "root" {
  path = find_in_parent_folders("root.hcl")
}

dependency "infrastructure" {
  config_path = "../infrastructure"

  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan"]
  mock_outputs = {
    kubeconfig = "ci-mock-kubeconfig"
  }
}

inputs = {
  kubeconfig = dependency.infrastructure.outputs.kubeconfig
}
