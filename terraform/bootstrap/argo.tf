resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  namespace        = "argocd"
  create_namespace = true
  version          = "9.2.2"

  values = [
    yamlencode({
      server = {
        extraArgs = ["--insecure"]
      }
      notifications = { enabled = false }
    })
  ]
}