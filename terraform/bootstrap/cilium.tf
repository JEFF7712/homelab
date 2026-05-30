resource "helm_release" "cilium" {
  name       = "cilium"
  repository = "https://helm.cilium.io/"
  chart      = "cilium"
  version    = "1.19.4"
  namespace  = "kube-system"

  set = [
    {
      name  = "ipam.mode"
      value = "kubernetes"
    },
    {
      name  = "kubeProxyReplacement"
      value = "true"
    },
    {
      name  = "cgroup.autoMount.enabled"
      value = "false"
    },
    {
      name  = "cgroup.hostRoot"
      value = "/sys/fs/cgroup"
    },
    {
      name  = "k8sServiceHost"
      value = "localhost"
    },
    {
      name  = "k8sServicePort"
      value = "7445"
    },
    {
      name  = "bgpControlPlane.enabled"
      value = "true"
    },
    {
      name  = "externalIPs.enabled"
      value = "true"
    },
    # L2 announcement: Cilium answers ARP for LB IPs on the cluster LAN
    # so off-cluster hosts (e.g. the NetBird LXC peer) can reach them
    # without depending on OPNsense proxy-ARP. Coexists with BGP.
    {
      name  = "l2announcements.enabled"
      value = "true"
    },
    # Default k8sClientRateLimit is too low for L2 leader election traffic
    # (Cilium does a Lease per announced IP); per Cilium docs, bump it.
    {
      name  = "k8sClientRateLimit.qps"
      value = "10"
    },
    {
      name  = "k8sClientRateLimit.burst"
      value = "20"
    },
    {
      name  = "securityContext.privileged"
      value = "true"
    },
    {
      name  = "operator.replicas"
      value = "1"
    }
  ]
}