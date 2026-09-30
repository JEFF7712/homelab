# homelab

NixOS homelab desired state.

This repository is the source of truth for the new homelab's host, network,
Kubernetes, and external-system configuration. Runtime state and deployment
evidence are kept separate from desired state.

## Ownership

- `flake/` owns NixOS hosts, disks, host networking, k3s installation, and host secrets.
- `tofu/` owns API-managed external systems: Cloudflare tunnel config and supported OPNsense resources.
- `opnsense_reconciler/` owns OPNsense interface and FRR settings absent from the OpenTofu provider.
- `gitops/` owns all Kubernetes objects through Flux.
- `home-assistant/` owns source-first Home Assistant configuration.
- `registry/` pins container images in `images.lock.json`; the registry service
  itself runs on `nas-01` via `flake/`. See
  [`docs/runbooks/local-registry.md`](docs/runbooks/local-registry.md).
- `config/agent-workspaces/`, `scripts/agent/`, and the client adapters own
  agent workspaces and workflow tooling. See
  [`docs/agent-workflow.md`](docs/agent-workflow.md).
- `secrets/` contains only SOPS-encrypted material.

## Deployment

First installation uses `nixos-anywhere`. Subsequent NixOS activation uses `nixos-rebuild switch` via `scripts/deploy_fleet.py` from the NAS-hosted GitLab runner. OpenTofu applies and OPNsense reconciliation run only in CI. Flux reconciles Kubernetes state from Git.

Do not apply infrastructure from a laptop.

## CI

`nas-privileged` is the protected NAS runner for deployment, OPNsense, and
registry import jobs. `nas-ci` handles validation across branches and merge
requests. See [`docs/runbooks/ci-pipelines.md`](docs/runbooks/ci-pipelines.md)
for runner setup, validation lanes, and path-based routing.

## Local development

See [`docs/runbooks/local-development.md`](docs/runbooks/local-development.md)
for the pinned environment, registry commands, and the offline validation gate.
