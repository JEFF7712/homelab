**NOTE - This repository is a mirror.** Active development happens on [Forgejo](https://git.rupan.dev/JEFF7712/homelab).

# homelab

Seven nodes of NixOS in a 10-inch rack, everything declared in code. See [HARDWARE.md](HARDWARE.md) for the full
parts list.

## Compute

k3s across `homelab-01` through `homelab-05`, two with NVIDIA GPUs (T1000,
T600). A NAS box holds storage and a local container registry; a thin client
runs AdGuard and NetBird; routing is a dedicated OPNsense box.

## Network

OPNsense router with BGP peering, Cloudflare Tunnel for remote access,
cert-manager for internal TLS.

## Runs here

- **Media**: Jellyfin plus the *arr suite, audiobooks, and Spotify sync
- **Voice**: Jarvis, a local voice assistant (Whisper, Piper, satellite mics)
- **Photos**: Immich
- **Smart home**: source-first Home Assistant, all config in this repo
- **Websites**: a handful of personal static sites
- **Observability**: Prometheus, Grafana, Loki, ntfy alerts
- **Backups**: database dumps to R2

## How it's managed

NixOS via flakes and the fleet deployer, firewall via OpenTofu plus a small
reconciler, and Kubernetes via Flux. Protected CI owns provider changes and
approved fleet operations; Flux reconciles Kubernetes state from Forgejo. The
repo is the source of truth; live state is verified, never hand-edited. See
[AGENT_MAP.md](AGENT_MAP.md) for owning source, minimum checks, and runbooks
before making or applying changes.