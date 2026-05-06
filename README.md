# Homelab

[![Kubernetes](https://img.shields.io/badge/Kubernetes-v1.35.0-326CE5?logo=kubernetes&logoColor=white)](https://kubernetes.io/)
[![Talos Linux](https://img.shields.io/badge/Talos-v1.12.0-FF6C2C?logo=linux&logoColor=white)](https://www.talos.dev/)
[![Terraform](https://img.shields.io/badge/Terraform-IaC-7B42BC?logo=terraform&logoColor=white)](https://www.terraform.io/)
[![ArgoCD](https://img.shields.io/badge/ArgoCD-GitOps-EF7B4D?logo=argo&logoColor=white)](https://argoproj.github.io/cd/)
[![Cilium](https://img.shields.io/badge/Cilium-v1.18.5-F8C517?logo=cilium&logoColor=black)](https://cilium.io/)
[![Ansible](https://img.shields.io/badge/Ansible-Automation-EE0000?logo=ansible&logoColor=white)](https://www.ansible.com/)
[![Renovate](https://img.shields.io/badge/Renovate-enabled-1F6FEB?logo=renovatebot&logoColor=white)](https://www.mend.io/renovate/)
[![Reloader](https://img.shields.io/badge/Reloader-auto_reload-3DDC84?logo=kubernetes&logoColor=white)](https://github.com/stakater/Reloader)

A production-grade, GitOps-managed Kubernetes homelab running on recycled enterprise hardware. This infrastructure demonstrates modern cloud-native patterns including immutable infrastructure, declarative configuration, zero-trust networking, and complete infrastructure-as-code.

## Architecture Overview

This homelab is built on several key principles:
- **Immutable Infrastructure**: Talos Linux nodes configured entirely through API, no SSH access
- **GitOps**: All cluster state managed by ArgoCD, synchronized from this repository
- **Zero Trust**: Identity-based access via NetBird mesh VPN and Cloudflare Access
- **Infrastructure as Code**: Terraform manages cluster provisioning, Kubernetes manifests define application state

## Hardware

The cluster runs on recycled enterprise thin clients and mini PCs:

| Device | Specs | Role |
|--------|-------|------|
| HP Chromebook 14 G5 | Intel Celeron N3350, 4GB RAM, 24GB eMMC | NetBird gateway |
| HP Mini | Intel i5-6500T, 16GB RAM, 500GB SSD + 1TB NVMe | Proxmox host (Control Plane VM, OPNsense VM, NetBird LXC, AdGuard LXC) |
| CLI ar9070 (×2) | AMD Ryzen V1605B, 16GB RAM, 60GB SSD | Talos worker nodes |
| Dell WYSE 5070 | Intel J4105, 8GB RAM, 64GB SSD + 2TB HDD | Talos worker node |

**Total Cluster Resources**: ~60GB RAM and ~3.6TB storage across NVMe + SATA SSDs and HDDs.

## Technical Stack

### Core Infrastructure
- **Hypervisor**: Proxmox VE
- **OS**: [Talos Linux v1.12.0](https://www.talos.dev/) - Immutable, API-managed Kubernetes OS
- **Kubernetes**: v1.35.0
- **IaC**: 
  - [Terraform](https://www.terraform.io/) - Infrastructure provisioning
  - [Terragrunt](https://terragrunt.gruntwork.io/) - DRY Terraform wrapper for managing multiple environments and remote state
  - [Ansible](https://www.ansible.com/) - Configuration management for non-Kubernetes infrastructure
- **GitOps**: ArgoCD v9.2.2

### Networking
- **CNI**: [Cilium v1.18.5](https://cilium.io/) - eBPF-based networking with KubeProxy replacement
- **BGP**: Cilium BGP Control Plane for dynamic service IP advertisement
- **Ingress**: Traefik v3 with LoadBalancer service (Cilium BGP/L2 announcements)
- **Tunnel**: Cloudflare Tunnel for zero-trust external access
- **VPN Overlay**: NetBird WireGuard mesh for remote access
- **Router/Firewall**: OPNsense VM

### Storage
- **CSI**: [Longhorn v1.10.1](https://longhorn.io/) - Distributed block storage
- **Storage Backend**: NVMe and SATA SSDs + HDD mounted via Talos `extraMounts` at `/var/mnt/longhorn`
- **Replication**: Single replica (For now)

### Observability
- **Metrics**: Grafana Mimir
- **Logs**: Grafana Loki
- **Collection**: Grafana Alloy
- **Visualization**: Grafana

### Applications
- **Media Stack**: Sonarr, Radarr, Lidarr, Prowlarr, Navidrome, Jellyfin, qBittorrent
- **Certificates**: cert-manager with Let's Encrypt
- **Config Reloads**: Reloader auto-rolls pods on ConfigMap/Secret changes
- **Dependency Updates**: Renovate automates dependency PRs

## Highlights

### Immutable Infrastructure
Talos Linux eliminates configuration drift. All node configuration happens via `machineconfig` patches in Terraform. No SSH, no manual intervention. Node upgrades are declarative and atomic.

### GitOps Everything
The cluster state is defined entirely in Git. ArgoCD continuously reconciles manifests from this repository. Changes are deployed through pull requests, providing full audit trails and rollback capability.

### Zero Trust Networking
**No open ports to the internet.** All access is identity-based:

- **Remote Access**: NetBird WireGuard mesh (Chromebook & LXC routing peers) advertises `10.0.20.0/24` to remote devices
- **Public Services**: Cloudflare Tunnel connects cluster to edge without exposed ports
- **Authentication**: Cloudflare Access protects public endpoints (`*.rupan.dev`)

### Cilium Networking
**eBPF-based CNI** replacing kube-proxy with native BGP and L2 capabilities:

- **BGP Control Plane**: Cluster (AS 64513) peers with OPNsense (AS 64512) at `10.0.20.1`, automatically advertising LoadBalancer IPs (`10.0.20.180-200`)
- **KubePrism**: Local API server access via `localhost:7445` for sidecars
- **Benefits**: Automatic failover, zero manual route configuration, graceful restart

### Declarative Everything
The entire stack is code: **Terraform** provisions VMs and Talos configs → **Terraform** installs Cilium/ArgoCD → **Ansible** bootstraps NetBird gateways → **ArgoCD** syncs all applications from Git.

### Application Deployment
All changes via Git → ArgoCD auto-syncs. Monitor in ArgoCD UI.

## Notes
- Storage is single-replica due to limited node count and disk space constraints.
- Hardware is budget-friendly equipment mostly off eBay and AliExpress.
- Power consumption: ~60W (estimate) idle for the entire cluster.

## Services & Applications

### Infrastructure Components
- **[Cilium](https://cilium.io/)** - eBPF-based Kubernetes CNI and networking
- **[Traefik](https://traefik.io/)** - Cloud-native ingress controller and reverse proxy
- **[cert-manager](https://cert-manager.io/)** - Automated TLS certificate management
- **[Longhorn](https://longhorn.io/)** - Distributed block storage for Kubernetes
- **[ArgoCD](https://argoproj.github.io/cd/)** - Declarative GitOps continuous delivery
- **[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)** - Secure tunnel for external access without open ports
- **[NetBird](https://netbird.io/)** - WireGuard-based mesh VPN network

### Automation & Workflow
- **[n8n](https://n8n.io/)** - Self-hosted workflow automation platform
- **[Redis](https://redis.io/)** - In-memory data store for n8n queue management
- **[Renovate](https://www.mend.io/renovate/)** - Automated dependency updates via PRs
- **[Reloader](https://github.com/stakater/Reloader)** - Auto-reloads pods on ConfigMap/Secret changes

### Media Management (Arr Stack)
- **[Sonarr](https://sonarr.tv/)** - TV series management and automation
- **[Radarr](https://radarr.video/)** - Movie collection manager
- **[Lidarr](https://lidarr.audio/)** - Music collection management
- **[Prowlarr](https://prowlarr.com/)** - Indexer manager for *arr applications
- **[qBittorrent](https://www.qbittorrent.org/)** - BitTorrent client
- **[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)** - Proxy server to bypass Cloudflare protection
- **[Navidrome](https://www.navidrome.org/)** - Self-hosted music streaming server

### Observability (Infrastructure)
- **[Grafana](https://grafana.com/)** - Metrics visualization and dashboards
- **[Grafana Mimir](https://grafana.com/oss/mimir/)** - Scalable long-term metrics storage
- **[Grafana Loki](https://grafana.com/oss/loki/)** - Log aggregation system
- **[Grafana Alloy](https://grafana.com/docs/alloy/)** - OpenTelemetry collector for metrics and logs
- **[MinIO](https://min.io/)** - S3-compatible object storage backend

### Security & Networking
- **[OPNsense](https://opnsense.org/)** - FreeBSD-based firewall and router
- **[Cloudflare Access](https://www.cloudflare.com/products/zero-trust/access/)** - Zero-trust identity-based access control
- **[AdGuard Home](https://adguard.com/en/adguard-home/overview.html)** - DNS server with ad blocking and privacy protection

---
