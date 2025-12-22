include "root" {
  path = find_in_parent_folders("root.hcl")
}

dependency "infrastructure" {
  config_path = "../infrastructure" 
}

inputs = {
  kubeconfig = dependency.infrastructure.outputs.kubeconfig
}