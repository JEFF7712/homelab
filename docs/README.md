# Documentation index

Start with [README](../README.md) for the system overview and [Agent Map](../AGENT_MAP.md)
for owning source, checks and runbooks. [AGENTS.md](../AGENTS.md) owns the operating
and authorization contract; runbooks describe procedures within that contract.
Source declarations establish desired state; only fresh runtime observations
establish what is deployed or healthy.

## Workflow reference

- [Agent workflow](agent-workflow.md): commands, task continuity, clients and verification evidence.
- [Task state decision](decisions/0001-agent-workflow-state.md): rationale for local task records.
- [Topology](../flake/topology/README.md): generate declared infrastructure diagrams.

## Runbooks

These are maintained procedures. Read the one matching the task and inspect its
owning source before applying changes. Dated observations inside a runbook are
evidence from that time, not permanent health or capacity guarantees.

### Workflow and delivery

- [Shipping changes](runbooks/ship.md)
- [Merge policy](runbooks/merge-policy.md)
- [Approvals dashboard](runbooks/approvals.md)
- [Local development](runbooks/local-development.md)
- [Remote interactive coding sessions](runbooks/remote-agent-sessions.md)
- [Agent workspace operations](runbooks/agent-workspaces.md)
- [projectctl](runbooks/projectctl.md)
- [Registry node credential rotation](runbooks/projectctl-registry-rotation.md)

### CI, releases and recovery

- [CI pipelines](runbooks/ci-pipelines.md)
- [Application CI](runbooks/application-ci.md)
- [Forgejo automation runtime](runbooks/forgejo-automation-runtime.md)
- [Local container registry operations](runbooks/local-registry.md)
- [Node supply](runbooks/node-supply.md)
- [Matrix releases](runbooks/matrix-release.md)
- [NixOS host deployment](runbooks/nixos-host-deployment.md)
- [NixOS artifact publication](runbooks/nixos-publication.md)
- [GitHub release synchronization](runbooks/release-mirror.md)
- [Repository health monitor](runbooks/repo-monitor.md)
- [Forgejo deployment and disaster recovery](runbooks/forgejo-disaster-recovery.md)
- [Independent platform recovery](runbooks/sovereign-platform-recovery.md)

### Network and platform

- [Shared fleet inventory](runbooks/fleet-inventory.md)
- [OPNsense configuration](runbooks/opnsense-configuration.md)
- [Read-only OPNsense access for agents](runbooks/opnsense-agent-access.md)
- [OPNsense BGP Proof](runbooks/opnsense-bgp-proof.md)
- [OPNsense Recovery](runbooks/opnsense-recovery.md)
- [NetBird management API for agents](runbooks/netbird.md)
- [Cloudflare Tunnel `homelab`](runbooks/cloudflare-tunnel.md)
- [Network outage monitor](runbooks/network-monitor.md)
- [homelab-04 Ethernet transmit hang](runbooks/homelab04-network.md)
- [homelab-05 Ethernet transmit hang](runbooks/homelab05-network.md)
- [Pod Security](runbooks/pod-security.md)
- [Secret handling](runbooks/secrets.md)
- [PostgreSQL Disaster Recovery](runbooks/postgres-disaster-recovery.md)
- [Akshath Omarchy VM](runbooks/omarchy-vm.md)

### Applications and devices

- [Home Assistant Configuration and UI Adoption Runbook](runbooks/home-assistant-configuration.md)
- [Home Assistant Automation Best Practices](runbooks/home-assistant-automation-best-practices.md)
- [Jarvis voice runbook](runbooks/jarvis-voice.md)
- [Kiosk display](runbooks/kiosk-display.md)
- [Roku Bulb Bridge Architecture](runbooks/roku-bridge-architecture.md)
- [Web analytics (Umami)](runbooks/analytics.md)
- [Media download-chain credentials](runbooks/arr-credentials.md)
- [Lidarr queue hygiene](runbooks/arr-queue-hygiene.md)
- [Bookshelf state backup and recovery](runbooks/bookshelf-recovery.md)
- [Game downloads and library](runbooks/games.md)
- [NAS game share](runbooks/nas-game-share.md)
- [pod-agent on the homelab](runbooks/pod-agent.md)

## Network references and code pitfalls

- [Switch port map](network/switch-port-map.md): physical connections.
- [Outbound NAT](network/opnsense-nat.md): mode prerequisite and reconciled rules.
- [Nix heredoc indentation](gotchas/nix-heredoc-indentation.md): runtime Python indentation failures.
- [Govee LAN control](gotchas/govee-lan-control.md): device protocol observations.

## Architecture and historical records

- [Persistent agent architecture](architecture/persistent-agent.md) and [task protocol](protocols/agent-tasks.md): controller implementation and deployment gates; not deployed.
- [Agent workflow specification](agent-workflow-spec.md): historical implementation spec; current behavior belongs to the workflow guide.
- `docs/superpowers/specs/` and `docs/superpowers/plans/`: dated designs and implementation plans.
- `docs/research/`: dated investigations and acceptance evidence, including [pod-agent release history](research/2026-10-07-pod-agent-release-history.md).
- `docs/agent-tasks/`: point-in-time task exports; check file history before using a handoff. Local task state and raw runtime evidence remain in ignored `.agent-state/`.

Historical records can contain superseded paths, commands, approvals and proposed
work. They do not override current source, the operating contract or runbooks.
Retrieve removed obsolete guidance from Git history when investigating an old incident.
