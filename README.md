**NOTE - This repository is a mirror.** Active development happens on [Forgejo](https://git.rupan.dev/JEFF7712/homelab).

# homelab

![healthchecks](https://healthchecks.io/badge/7cef9d84-fe01-44d6-bfe7-51d303/5hX7D_7s-2.svg)
[![pipeline](https://ci.rupan.dev/api/badges/1/status.svg)](https://ci.rupan.dev/repos/JEFF7712/homelab)

Seven nodes of NixOS in a 10-inch rack, everything declared in code. See [HARDWARE.md](HARDWARE.md) for the full
parts list.

## Compute

k3s across `homelab-01` through `homelab-05`, two with NVIDIA GPUs (T1000,
T600) for voice STT, plus Intel iGPU transcoding for Jellyfin. NAS
holds storage, a local container registry, and a Nix binary cache; a thin client
runs AdGuard and NetBird; routing is a dedicated OPNsense box.

## Network

OPNsense router with BGP peering, Cloudflare Tunnel for remote access,
cert-manager for internal TLS.

## Runs here

- **Media**: Jellyfin plus the *arr suite, Seerr requests, an AI
  recommender, Navidrome music, Calibre ebooks, Spotify sync, and Immich
- **Music**: Music Assistant with LedFx lighting effects
- **Voice**: Local voice assistant (Whisper, Piper, satellite mics)
- **Smart home**: source-first Home Assistant, all config in this repo
- **Notes**: Obsidian LiveSync (CouchDB) with a Quartz published site
- **Websites**: personal static sites plus live apps
- **Analytics**: Umami web analytics
- **Observability**: Prometheus, Grafana, Loki, ntfy alerts, outage
  heartbeats, repo health monitor
- **Automation**: cluster sentinel, Renovate agents, website monitors
- **Backups**: database dumps to R2
- **Self-hosted**: git (Forgejo), CI (Woodpecker), container registry (Zot),
  and Nix binary cache (Attic)

## How it's managed

NixOS via flakes and the fleet deployer, firewall via OpenTofu plus a small
reconciler, and Kubernetes via Flux. Protected CI owns provider changes and
approved fleet operations; Flux reconciles Kubernetes state from Forgejo. The
repo is the source of truth; live state is verified, never hand-edited. See
[AGENT_MAP.md](AGENT_MAP.md) for owning source, minimum checks, and runbooks
before making or applying changes.