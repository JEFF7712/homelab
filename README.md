# homelab

![healthchecks](https://healthchecks.io/badge/7cef9d84-fe01-44d6-bfe7-51d303/5hX7D_7s-2.svg)
[![pipeline](https://ci.rupan.dev/api/badges/1/status.svg)](https://ci.rupan.dev/repos/JEFF7712/homelab)

Seven nodes of NixOS in a 10-inch rack, everything declared in code. See [HARDWARE.md](HARDWARE.md) for the full
parts list.

## Capacity

Hardware snapshot checked on 2026-11-11. The seven NixOS hosts were queried
live; router figures come from [HARDWARE.md](HARDWARE.md).

| Systems | CPU cores / threads | Installed RAM | Raw disk |
| --- | --- | --- | --- |
| k3s — 5 nodes | 32 / 52 | 112 GiB | 3.68 TB |
| NAS | 6 / 12 | 16 GiB | 12.51 TB |
| AdGuard / NetBird | 4 / 4 | 8 GiB | 1.06 TB |
| **NixOS subtotal — 7 hosts** | **46 / 72** | **136 GiB** | **17.25 TB** |
| OPNsense router (inventory) | 4 / 4 | 8 GiB | 32 GB |
| **Total — 8 machines** | **50 / 76** | **144 GiB** | **17.29 TB** |

- **GPUs:** NVIDIA T1000 + T600, 4 GiB VRAM each (8 GiB combined),
  plus integrated graphics for media acceleration.
- **Storage:** the NixOS hosts have 12 physical drives: 9 SSDs/NVMe
  (4.25 TB) and 3 HDDs (13.00 TB), including the 1 TB AdGuard backup disk.
  Capacities use decimal TB/GB and are rounded after summing device sizes;
  RAM/VRAM use binary GiB. Raw disk includes both sides of the NAS boot
  mirror and is not usable or free capacity. The NAS data pool is on the
  single 10 TB HDD.
- **Network:** gigabit switching and most host links; the NAS has a 2.5 GbE
  onboard NIC. See [HARDWARE.md](HARDWARE.md) for the parts list.

Totals exclude the Chromebook, network appliances' internal resources, spare
unattached disks, and virtual disks/RAM-backed swap. Installed RAM is nominal
module capacity; the OS reports less after hardware reservations.

## Compute

k3s across `homelab-01` through `homelab-05`, two with NVIDIA GPUs (T1000,
T600) for voice STT, plus Intel iGPU transcoding for Jellyfin. NAS
holds storage, a local container registry, and a Nix binary cache; a thin client
runs AdGuard and NetBird; routing is a dedicated OPNsense box.

## Network

OPNsense router with BGP peering, Netbird and Cloudflare Tunnel for remote access,
cert-manager for internal TLS.

## Runs here

- **Media**: Jellyfin plus the *arr suite, Seerr requests, an AI
  recommender, Navidrome music, Calibre ebooks, Spotify sync, and Immich
- **Music**: Music Assistant with LedFx lighting effects
- **Voice**: Local Nemotron recognition, satellite mics, and display-only replies
- **Smart home**: source-first Home Assistant, all config in this repo
- **Notes**: Obsidian LiveSync (CouchDB) with a Quartz published site
- **Websites**: personal static sites plus live apps
- **Analytics**: Umami web analytics
- **Observability**: Prometheus, Grafana, Loki, ntfy alerts, outage
  heartbeats, repo health monitor
- **Automation**: cluster sentinel, Renovate agents, website monitors
- **Backups**: database dumps to R2
- **Self-hosted**: git (Forgejo), CI (Woodpecker), container registry (Zot),
  and Nix binary cache (niks3)

## How it's managed

NixOS via flakes and the fleet deployer, firewall via OpenTofu plus a small
reconciler, and Kubernetes via Flux. Protected CI owns provider changes and
fleet operations within the task scope; Flux reconciles Kubernetes state from Forgejo. The
repo is the source of truth; live state is verified, never hand-edited. See
[AGENT_MAP.md](AGENT_MAP.md) for owning source, minimum checks, and runbooks
before making or applying changes.
