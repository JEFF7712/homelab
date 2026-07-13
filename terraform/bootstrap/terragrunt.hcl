include "root" {
  path = find_in_parent_folders("root.hcl")
}

dependency "infrastructure" {
  config_path = "../infrastructure"

  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan"]
  mock_outputs = {
    kubeconfig = <<-EOT
      apiVersion: v1
      kind: Config
      current-context: ci
      clusters:
        - name: ci
          cluster:
            server: https://127.0.0.1:6443
            certificate-authority-data: Cg==
      contexts:
        - name: ci
          context:
            cluster: ci
            user: ci
      users:
        - name: ci
          user:
            client-certificate-data: Cg==
            client-key-data: Cg==
    EOT
  }
}

inputs = {
  kubeconfig = dependency.infrastructure.outputs.kubeconfig
}
