# Homelab Infrastructure

[![Kubernetes](https://img.shields.io/badge/Kubernetes-v1.35.0-326CE5?logo=kubernetes&logoColor=white)](https://kubernetes.io/)
[![Talos Linux](https://img.shields.io/badge/Talos-v1.12.0-FF6C2C?logo=linux&logoColor=white)](https://www.talos.dev/)
[![Terraform](https://img.shields.io/badge/Terraform-IaC-7B42BC?logo=terraform&logoColor=white)](https://www.terraform.io/)
[![ArgoCD](https://img.shields.io/badge/ArgoCD-GitOps-EF7B4D?logo=argo&logoColor=white)](https://argoproj.github.io/cd/)
[![Cilium](https://img.shields.io/badge/Cilium-v1.18.5-F8C517?logo=cilium&logoColor=black)](https://cilium.io/)
[![Ansible](https://img.shields.io/badge/Ansible-Automation-EE0000?logo=ansible&logoColor=white)](https://www.ansible.com/)

A production-grade, GitOps-managed Kubernetes homelab running on recycled enterprise hardware. This infrastructure demonstrates modern cloud-native patterns including immutable infrastructure, declarative configuration, zero-trust networking, and complete infrastructure-as-code.

## Architecture Overview

This homelab is built on several key principles:
- **Immutable Infrastructure**: Talos Linux nodes configured entirely through API, no SSH access
- **GitOps**: All cluster state managed by ArgoCD, synchronized from this repository
- **Zero Trust**: Identity-based access via NetBird mesh VPN and Cloudflare Access
- **Infrastructure as Code**: Terraform manages cluster provisioning, Kubernetes manifests define application state

```
┌─────────────────────────────────────────────────────────────────┐
│                       Cloudflare Edge                           │
│  (TLS Termination, Zero Trust Access, DDoS Protection)          │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                   ┌───────────────────┐
                   │ Cloudflare Tunnel │
                   │   (cloudflared)   │
                   └─────────┬─────────┘
                             │
        ┌────────────────────┴───────────────────┐
        │         Kubernetes Cluster             │
        │                                        │
        │  ┌──────────────────────────────────┐  │
        │  │      Traefik Ingress (v3)        │  │
        │  └──────────────────────────────────┘  │
        │                                        │
        │  ┌──────────┐  ┌──────────┐  ┌──────┐  │
        │  │   Apps   │  │  Media   │  │ n8n  │  │
        │  └──────────┘  └──────────┘  └──────┘  │
        │                                        │
        │  ┌──────────────────────────────────┐  │
        │  │   Longhorn Distributed Storage   │  │
        │  └──────────────────────────────────┘  │
        │                                        │
        │  ┌──────────────────────────────────┐  │
        │  │      Cilium CNI (eBPF)           │  │
        │  └──────────────────────────────────┘  │
        └────────────────────────────────────────┘
                 │              │              │
        ┌────────▼──────┐  ┌────▼────┐  ┌──────▼─────┐
        │ Control Plane │  │ Worker  │  │  Worker    │
        │   (Proxmox)   │  │  Node   │  │   Node     │
        └───────────────┘  └─────────┘  └────────────┘
                             
┌─────────────────────────────────────────────────────────────────┐
│                    NetBird Mesh Network                         │
│  (WireGuard overlay for remote access to services)              │
│        Laptop ←→ OPNsense Gateway ←→ Cluster Services           │
└─────────────────────────────────────────────────────────────────┘
```

## Hardware

The cluster runs on recycled enterprise thin clients and mini PCs:

| Device | Specs | Role |
|--------|-------|------|
| HP Chromebook 14 G5 | Intel Celeron N3350, 4GB RAM, 24GB eMMC | NetBird gateway, auxiliary services |
| HP Mini | Intel i5-6500T, 16GB RAM, 500GB SSD + 1TB NVMe | Proxmox host (Control Plane VM, OPNsense VM, NetBird LXC) |
| CLI ar9070 (×2) | AMD Ryzen V1605B, 16GB RAM, 60GB SSD | Talos worker nodes |
| Dell WYSE 5070 | Intel J4105, 8GB RAM, 64GB SSD | Talos worker node |

**Total Cluster Resources**: ~48GB RAM, ~1.6TB storage across NVMe and SATA SSDs

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
- **Ingress**: Traefik v3 with L2 LoadBalancer (Cilium L2 announcements)
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
│   ├── n8n/                # n8n deployment, workers, Redis
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
Talos Linux eliminates configuration drift. All node configuration happens via `machineconfig` patches in Terraform—no SSH, no manual intervention. Node upgrades are declarative and atomic.

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

**No open ports to the internet.** Access is identity-based:

- **Remote Access**: NetBird WireGuard mesh connects devices to cluster services via OPNsense routing peer
- **Public Services**: Cloudflare Tunnel (`cloudflared`) connects cluster to Cloudflare edge
- **Authentication**: Cloudflare Access protects public endpoints (`*.rupan.dev`)

### Advanced Networking Architecture

#### Cilium with L2 Announcements
Replaces kube-proxy with eBPF programs for superior performance. L2 load balancer announcements enable LoadBalancer services on bare metal.

#### KubePrism
Talos-native component that provides local API server access via `localhost:7445`, enabling sidecar containers to communicate with Kubernetes API.

### Storage with Longhorn
Multi-disk configuration with NVMe primary storage and SATA secondary volumes. Storage is mounted directly to worker nodes via Talos `extraMounts`:

```yaml
# Control Plane: 1TB NVMe + 300GB SATA
# Workers: 60-64GB system disks + storage volumes
```

### Ansible for Hybrid Infrastructure
While Kubernetes workloads are managed via GitOps, Ansible orchestrates the **non-containerized infrastructure** that can't be managed by Talos or Kubernetes:

- **NetBird Gateway**: Bootstraps WireGuard mesh on Alpine Linux (Chromebook & LXC)
- **Maintenance**: Cross-platform updates for Alpine, Debian, and OPNsense
- **Auditing**: Automated infrastructure health checks and service discovery
- **Multi-OS Support**: Manages Alpine (LXC/Chromebook), FreeBSD (OPNsense), and interacts with Talos via `talosctl`

```yaml
# Example: NetBird installation playbook
- name: Install NetBird on Alpine Gateway
  hosts: netbird_gateway
  tasks:
    - name: Install NetBird
      shell: curl -fsSL https://pkgs.netbird.io/install.sh | sh
    
    - name: Connect to Mesh
      command: netbird up --setup-key {{ netbird_setup_key }}
```

Ansible complements the immutable Kubernetes layer by managing the **mutable edge** devices that provide network connectivity and routing.

### Declarative Everything
From VM provisioning to application deployment, everything is code:

1. **Terraform** provisions Proxmox VMs and generates Talos machine configs
2. **Talos** bootstraps the Kubernetes cluster
3. **Terraform** installs Cilium and ArgoCD via Helm
4. **Ansible** Installs Netbird onto Chromebook & Proxmox LXC
5. **ArgoCD** synchronizes all applications from Git
6. **No manual kubectl apply commands**

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

## Network Architecture Deep Dive

### Cluster Networking (Cilium)
- **CNI**: Cilium (eBPF-based)
- **Service CIDR**: `10.96.0.0/12` (Kubernetes services)
- **Pod CIDR**: `10.244.0.0/16`
- **KubePrism**: Enabled for local API access
- **Features**: L2 announcements, kube-proxy replacement, external IPs

### Remote Access (NetBird Overlay)
- **Technology**: WireGuard mesh VPN
- **Topology**: HA Peer-to-peer with Proxmox LXC and Chromebook as routing peers
- **Advertised Routes**: `10.0.20.0/24` (cluster devices) and LAN networks
- **Use Case**: Secure remote access to cluster services and homelab devices without exposing ports

### Public Ingress (Cloudflare + Traefik)
```
Internet → Cloudflare Edge → Cloudflare Tunnel → Traefik → Services
         (DDoS, TLS, Auth)    (No open ports)   (Routing)
```

- **Ingress Controller**: Traefik v3 with LoadBalancer service
- **Tunnel**: Cloudflare Tunnel connects cluster to Cloudflare network
- **TLS**: Automated via cert-manager + Let's Encrypt
- **Access Control**: Cloudflare Access provides identity-based authentication

## Security Posture

- **No SSH**: Talos API provides secure, auditable node management
- **Immutable OS**: Read-only root filesystem, no package manager
- **Network Segmentation**: Separate networks for management, services, and storage
- **Zero Trust Access**: All external access requires authentication
- **Automated Certificates**: cert-manager with Let's Encrypt for TLS
- **Pod Security**: Namespace-level pod security standards enforced

## Management

### Cluster Operations
```bash
# All operations via talosctl API
talosctl -n <node-ip> dashboard
talosctl -n <node-ip> logs
talosctl -n <node-ip> get members

# No SSH, ever
```

### Application Deployment
All changes happen through Git:
1. Modify manifests in `infrastructure/` or `apps/`
2. Commit and push to repository
3. ArgoCD automatically syncs changes
4. Monitor in ArgoCD UI

### Infrastructure Changes
```bash
# Modify Terraform configurations
cd terraform/infrastructure
nano cluster.tf

# Apply changes
terragrunt apply

# Talos handles rolling updates gracefully
```

### Ansible Operations
Ansible manages non-Kubernetes infrastructure across the homelab:

```bash
# Run maintenance updates across all infrastructure
cd ansible
ansible-playbook playbooks/maintenance.yml

# Audit infrastructure health and disk usage
ansible-playbook playbooks/audit.yml

# Discover running services across all hosts
ansible-playbook playbooks/service-scan.yml

# Bootstrap NetBird on new gateway nodes
ansible-playbook playbooks/netbird.yml --extra-vars "netbird_setup_key=YOUR_KEY"

# Target specific host groups
ansible-playbook playbooks/maintenance.yml --limit alpine_nodes
ansible-playbook playbooks/audit.yml --limit talos
```

**Inventory Structure:**
- `alpine_nodes`: Chromebook gateway (Alpine Linux)
- `talos`: Kubernetes cluster nodes (managed via talosctl)
- `bsd_nodes`: OPNsense router/firewall
- `netbird_gateway`: WireGuard mesh routing peers

**Key Playbook Features:**
- **Idempotent operations**: Safe to run repeatedly
- **Multi-OS support**: Handles Alpine (apk), Debian (apt), FreeBSD (OPNsense firmware)
- **Talos integration**: Uses `talosctl` for health checks without SSH
- **Docker awareness**: Detects and prunes Docker containers on applicable hosts

## Design Philosophy

This homelab prioritizes:
- **Reproducibility**: Destroy and rebuild the entire stack from code
- **Best Practices**: Production patterns on homelab hardware
- **Learning**: Hands-on experience with enterprise-grade tools
- **Automation**: Minimize manual intervention and configuration drift
- **Security**: Defense in depth with multiple security layers

## Notes

- Storage is single-replica due to limited node count and disk space constraints
- Hardware is budget-friendly recycled enterprise equipment
- Power consumption: ~60W (estimate) idle for the entire cluster

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

---