resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  namespace        = "argocd"
  create_namespace = true
  version          = "9.2.2"

  values = [
    <<-EOF
    server:
      service:
        type: LoadBalancer
      extraArgs:
        - --insecure
    EOF
  ]
}