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
| Dell WYSE 5070 | Intel J4105, 8GB RAM, 64GB SSD | Talos worker node |

**Total Cluster Resources**: ~60GB RAM, ~1.6TB storage across NVMe and SATA SSDs

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
- **Storage Backend**: NVMe and SATA SSDs mounted via Talos `extraMounts` at `/var/mnt/longhorn`
- **Replication**: Single replica (For now)

### Observability
- **Metrics**: Grafana Mimir
- **Logs**: Grafana Loki
- **Collection**: Grafana Alloy
- **Visualization**: Grafana

### Applications
- **Automation**: n8n (workflow automation)
- **Media Stack**: Sonarr, Radarr, Lidarr, Prowlarr, Navidrome, FlareSolverr, qBittorrent
- **Certificates**: cert-manager with Let's Encrypt
- **Config Reloads**: Reloader auto-rolls pods on ConfigMap/Secret changes
- **Dependency Updates**: Renovate automates dependency PRs
- **AI Services**: Infrastructure for AI workloads (n8n-worker-ai)

## Repository Structure

```
.
├── terraform/
│   ├── infrastructure/      # Proxmox VMs, Talos cluster provisioning
│   │   ├── cluster.tf       # Node definitions, machine configs
│   │   ├── opnsense.tf      # Router VM
│   │   └── netbird.tf       # NetBird LXC gateway configuration
│   └── bootstrap/           # Initial cluster bootstrap
│       ├── argo.tf          # ArgoCD Helm installation
│       └── cilium.tf        # Cilium CNI installation
│
├── apps/                    # ArgoCD Application manifests
│   ├── root-app.yaml        # App-of-apps pattern root
│   ├── media.yaml           # Media stack application
│   ├── longhorn.yaml        # Storage system
│   ├── traefik.yaml         # Ingress controller
│   ├── cloudflare-tunnel.yaml
│   ├── n8n.yaml             # Workflow automation
│   └── observability.yaml   # Monitoring stack
│
├── infrastructure/          # Kubernetes manifests by service
│   ├── media/              # Arr stack, Navidrome, torrents
│   ├── n8n/                # n8n deployment, workers, databases
│   ├── cloudflare/         # Tunnel deployment
│   ├── cert-manager/       # Certificate issuers
│   ├── cilium/             # IP pool configuration
│   └── observability/      # Grafana, Loki, Mimir, Alloy
│
├── ansible/                # Infrastructure automation
│   ├── inventory.ini       # Infrastructure hosts inventory
│   ├── ansible.cfg         # Ansible configuration
│   ├── requirements.yml    # Collection dependencies
│   └── playbooks/
│       ├── netbird.yml     # NetBird mesh VPN bootstrap
│       ├── maintenance.yml # OS updates and health checks
│       ├── audit.yml       # Disk usage and connectivity audits
│       └── service-scan.yml # Service discovery and mapping
│
└── bootstrap/
    └── root-app.yaml       # Initial ArgoCD sync point
```

## Highlights

### Immutable Infrastructure
Talos Linux eliminates configuration drift. All node configuration happens via `machineconfig` patches in Terraform. No SSH, no manual intervention. Node upgrades are declarative and atomic.

### GitOps Everything
The cluster state is defined entirely in Git. ArgoCD continuously reconciles manifests from this repository. Changes are deployed through pull requests, providing full audit trails and rollback capability.

```yaml
# ArgoCD App-of-Apps pattern
spec:
  source:
    repoURL: https://gitlab.com/JEFF7712/homelab.git
    path: apps
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

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

### Storage & Configuration Management
**Longhorn** provides distributed storage with NVMe/SATA SSDs mounted via Talos `extraMounts`. Control Plane: 1TB NVMe + 300GB SATA.

**Ansible** manages non-containerized infrastructure (NetBird gateways, OPNsense updates, Alpine/Ubuntu systems) that complements the immutable Kubernetes layer.

### Declarative Everything
The entire stack is code: **Terraform** provisions VMs and Talos configs → **Terraform** installs Cilium/ArgoCD → **Ansible** bootstraps NetBird gateways → **ArgoCD** syncs all applications from Git.

## Deployment Workflow

```bash
# 1. Provision infrastructure
cd terraform/infrastructure
terragrunt apply

# 2. Bootstrap cluster components
cd ../bootstrap
terragrunt apply

# 3. Bootstrap Netbird
cd ../ansible
ansible-playbook playbooks/netbird.yml

# 4. Deploy root ArgoCD application
kubectl apply -f bootstrap/root-app.yaml

# 5. ArgoCD handles the rest
# All apps in apps/ directory are automatically synced
```

## Management

### Cluster Operations
```bash
# Talos API (no SSH)
talosctl -n <node-ip> dashboard
talosctl -n <node-ip> logs

# Infrastructure changes
cd terraform/infrastructure && terragrunt apply

# Ansible operations
cd ansible
ansible-playbook playbooks/maintenance.yml  # OS updates
ansible-playbook playbooks/audit.yml        # Health checks
```

### Application Deployment
All changes via Git → ArgoCD auto-syncs. Monitor in ArgoCD UI.

## Design Philosophy

This homelab prioritizes:
- **Reproducibility**: Destroy and rebuild the entire stack from code
- **Best Practices**: Production patterns on homelab hardware
- **Learning**: Hands-on experience with enterprise-grade tools
- **Automation**: Minimize manual intervention and configuration drift
- **Security**: Defense in depth with multiple security layers

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
