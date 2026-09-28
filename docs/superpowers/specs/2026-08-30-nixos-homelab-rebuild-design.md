# NixOS Homelab Rebuild Design

## Status

Approved for implementation planning on 2026-08-30.

## Goal

Build a greenfield NixOS homelab whose repository is the desired state for hosts,
OPNsense, external services, Kubernetes, secrets, and recovery procedures. No
laptop applies are permitted after bootstrap.

## Scope

The target platform contains:

- OPNsense on the Dell Wyse 5070 Extended, already physically migrated from the prior VM.
- NixOS AdGuard and NetBird routing on the Dell Wyse 5070 Standard.
- A NixOS NAS on the Jonsbo N2, providing ZFS, NFS, Attic, backups, and a GitLab runner.
- A three-server NixOS k3s cluster on the HP EliteDesk Mini and two CLI ar9070 units.
- Cilium networking with kube-proxy replacement and BGP to OPNsense.
- Flux GitOps for every Kubernetes object.
- OpenTofu plus an OPNsense API reconciler for firewall desired state.

Out of scope:

- Proxmox, Talos, Longhorn, Flannel, kubeadm, Ceph, iSCSI, Sealed Secrets, colmena, comin, nixidy, and Vault.
- Kubernetes on the NAS, either Wyse appliance, or Chromebook.
- WAN-exposed administration services.

## Repository and ownership

`homelab` is an independent Git repository. It has three non-overlapping control planes:

```text
flake/                 NixOS hosts, disko, impermanence, deploy-rs, sops-nix
gitops/                Flux bootstrap and Kubernetes desired state
tofu/                  OpenTofu for OPNsense-supported and external APIs
opnsense-reconciler/   Typed API reconciliation for OPNsense provider gaps
secrets/               SOPS-encrypted material only
modules/               Shared NixOS role modules
hosts/                 Host-specific NixOS definitions
tests/                 Nix, schema, and configuration-contract tests
docs/                  Architecture, runbooks, and recovery procedures
```

The flake owns operating systems, disks, host networking, k3s installation, NFS clients, users, SSH, and persisted paths. Flux owns all Kubernetes objects. OpenTofu owns Cloudflare, NetBird, and OPNsense API resources supported by its pinned provider. The OPNsense reconciler owns only the interface assignment, L3 interface settings, and FRR global BGP settings missing from that provider.

First installation uses `nixos-anywhere`. Subsequent host activation uses `deploy-rs` from the NAS-hosted GitLab runner. The runner is outside k3s so it can deploy or repair a failed cluster. GitLab.com shared runners cannot reach the private VLANs.

## Network design

OPNsense owns routing, firewall policy, DHCP, DNS forwarding, and BGP peering. The managed switch trunks the relevant VLANs to OPNsense and to hosts that need multiple VLANs.

| VLAN | CIDR | Purpose |
|---|---|---|
| 10 | `10.0.10.0/24` | Network and host management |
| 20 | `10.0.20.0/24` | Trusted user clients |
| 30 | `10.0.30.0/24` | Infrastructure hosts and k3s nodes |
| 40 | `10.0.40.0/24` | Kubernetes LoadBalancer VIP pool |
| 50 | `10.0.50.0/24` | Guest and IoT, Internet-only |
| 60 | `10.0.60.0/24` | NetBird remote-client policy segment |

OPNsense has a stable address in each routed VLAN. DHCP reservations provide stable infrastructure addresses. Firewall rules default-deny between VLANs; they allow only documented service flows. External access is NetBird-only, except explicitly public applications served through Cloudflare Tunnel.

The OPNsense OpenTofu provider must be pinned and used for VLAN devices, Kea DHCP subnets and reservations, firewall rules, Unbound forwarding, and supported BGP objects. It cannot configure interface assignment or addresses, nor FRR global BGP state. `opnsense-reconciler` uses the OPNsense API to enforce only those missing settings, reads live state before changes, applies idempotently, and verifies the resulting state. It runs in CI immediately before the OpenTofu apply that depends on those settings. An encrypted OPNsense configuration backup is captured before every firewall reconciliation.

## Host and storage design

All NixOS hosts use explicit persistence and ephemeral roots. SSH host keys, SOPS age identities, service state, and machine identity are persisted. Root login and password authentication are disabled.

| Host | Role | Storage policy |
|---|---|---|
| `adguard-netbird-01` | Wyse Standard, AdGuard and NetBird routing peer | LUKS2 and Btrfs impermanence; persist AdGuard, NetBird, SSH, and SOPS state; TPM-backed unlock where hardware validation permits it |
| `nas-01` | Jonsbo N2, NFS, Attic, runner, backups | Mirrored NVMe ZFS system pool with impermanent root; single-disk `tank` initially, then attach second 10 TB disk as a mirror |
| `k3s-server-01` | HP Mini, preferred database placement | LUKS2 and Btrfs impermanence; local NVMe for k3s and fsync-heavy database volumes |
| `k3s-server-02` | CLI ar9070 | LUKS2 and Btrfs impermanence; local SSD for embedded-etcd member data |
| `k3s-server-03` | CLI ar9070 | LUKS2 and Btrfs impermanence; local SSD for embedded-etcd member data |

`tank` ZFS datasets are `media`, `photos`, `documents`, `backups`, `cluster`, and `attic`. `photos` and `documents` retain 48 hourly, 30 daily, and 12 monthly snapshots and copy daily to the 2 TB disk. `backups` and `cluster` retain 30 daily snapshots. `media` and `attic` retain seven daily snapshots and are not copied to the 2 TB disk. `tank` is not a redundant storage pool until the second 10 TB disk has been attached. (The planned `gitlab-runner` dataset was dropped: CI builds run from the runner's exec-capable `/tmp`, so there is nothing worth snapshotting.)

NFS is the default Kubernetes storage medium. PostgreSQL and other fsync-heavy databases use Mini-local volumes and are independently backed up to the NAS. The NAS is never a Kubernetes node.

## Cluster design

k3s uses embedded etcd across all three servers. This provides control-plane availability without new hardware. The Mini is the preferred scheduling target for database-heavy workloads, but all three servers participate in etcd.

k3s starts with `--flannel-backend=none` and `--disable-network-policy`. Cilium is installed before normal workloads. The pinned k3s release must have its kube-proxy suppression option verified in a disposable test cluster before production use. Cilium enables kube-proxy replacement only when its API endpoint is directly reachable by every agent.

Cilium BGP Control Plane v2 is the only BGP API used. It peers each selected k3s node to OPNsense over VLAN 30. Cilium allocates VIPs from an immutable reserved subrange in VLAN 40 using `CiliumLoadBalancerIPPool`; a narrowly scoped `CiliumBGPAdvertisement` advertises only labelled LoadBalancer services as /32 routes. OPNsense accepts active BGP sessions from nodes and permits TCP/179 only from their reserved addresses.

## GitOps and secrets design

Flux bootstraps once from `gitops/clusters/homelab-01` using a read-only GitLab deploy token. Renovate and CI write Git; Flux only reads and reconciles it.

Flux reconciliation layers are ordered as follows:

```text
sources -> CRDs/controllers -> SOPS bootstrap secrets -> External Secrets
        -> Cilium/storage/ingress -> platform services -> applications
```

Every layer uses explicit `dependsOn`, health checks, and `prune: true` where it owns disposable resources. Applications cannot reconcile until their required controllers and secrets are healthy.

SOPS encrypts the small set of Kubernetes bootstrap Secrets. This includes the restricted GitLab API token needed by External Secrets Operator. ESO then reads only scoped GitLab variables into workload Secrets. The ESO access token is not stored in GitLab Variables because that would make ESO depend on the backend it has not yet authenticated to read. Namespace-local `SecretStore` objects and separate GitLab access tokens limit the blast radius.

## Security, backup, and recovery

- Administrative access is restricted to management VLAN and NetBird policy.
- Host credentials use SSH keys only, and host secrets are SOPS-encrypted.
- Kubernetes runtime values are GitLab variables synchronized by ESO.
- Each k3s server creates encrypted embedded-etcd snapshots on the NAS.
- Mini-local databases have database-native backups written to NAS datasets.
- ZFS snapshots support operational recovery, not disaster recovery.
- Every firewall reconciliation captures an encrypted OPNsense configuration backup and records its revision with the CI job.
- No old platform is destroyed until the replacement passes its live health and recovery checks.

## Delivery sequence

1. Create repository contracts, development shell, CI, SOPS policy, remote OpenTofu state, and configuration tests.
2. Import the running OPNsense state; implement and validate OpenTofu plus the OPNsense reconciler against the new network design.
3. Reinstall the Wyse Standard as `adguard-netbird-01` and validate DNS and remote routing.
4. Build the NAS, burn in disks, validate ZFS, NFS, Attic, runner, and backups.
5. Bootstrap the three NixOS k3s servers, Cilium, BGP, and a LoadBalancer canary service.
6. Bootstrap Flux, then storage, ingress, observability, and applications by dependency layer.
7. Migrate data, perform restore tests, and decommission Proxmox, Talos, Longhorn, and old state only after the new paths are proven.

## Acceptance criteria

- CI evaluates every NixOS host and validates disk declarations, SOPS coverage, OpenTofu formatting and plans, Flux manifests, Kubernetes schemas, and secret leakage.
- OPNsense converges on a clean plan and the reconciler verifies interface and FRR state after every apply.
- Every NixOS host can reinstall reproducibly with `nixos-anywhere` and can subsequently deploy from the NAS runner.
- All three k3s servers are healthy, Cilium is ready, BGP sessions are established, and a canary LoadBalancer VIP is reachable through OPNsense.
- Flux reports all declared layers ready and prunes a deliberately removed disposable canary object.
- A sample NFS PVC, a Mini-local database backup, an etcd snapshot restore, and a NAS dataset restore are proven before old infrastructure is removed.

## Evidence

- k3s custom-CNI options: <https://docs.k3s.io/networking/basic-network-options>
- Cilium kube-proxy replacement: <https://docs.cilium.io/en/stable/network/kubernetes/kubeproxy-free/>
- Cilium BGP control plane v2: <https://docs.cilium.io/en/stable/network/bgp-control-plane/bgp-control-plane-configuration/>
- Cilium LoadBalancer IPAM: <https://docs.cilium.io/en/stable/network/lb-ipam/>
- Flux GitLab bootstrap: <https://fluxcd.io/flux/installation/bootstrap/gitlab/>
- Flux dependencies and pruning: <https://fluxcd.io/flux/components/kustomize/kustomizations/>
- External Secrets GitLab provider: <https://external-secrets.io/main/provider/gitlab-variables/>
- OPNsense provider coverage: <https://github.com/browningluke/terraform-provider-opnsense#current-api-coverage>
