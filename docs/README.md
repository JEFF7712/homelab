# Documentation Index

Read narrowly — pick the doc that matches the concern, don't read all of them. Plans under `docs/superpowers/plans/` are dated implementation records, not active state. Runbooks in `docs/runbooks/` are the authoritative procedure for live operations. `AGENT_MAP.md` is the cross-reference from source-of-truth concerns (hosts, modules, reconcilers) to their owning files and minimum validation.

### Workflow and agent tooling
- `docs/agent-workflow.md` — operator guide for `python -m scripts.agent` and `just` recipes. Start here when onboarding a new agent task.
- `docs/agent-workflow-spec.md` — formal spec backing `docs/agent-workflow.md`.
- `docs/superpowers/specs/2026-09-05-agent-workflow-design.md` — design rationale for the agent workflow.
- `docs/superpowers/plans/2026-09-05-agent-workflow.md` — implementation plan that landed the agent workflow.
- `docs/decisions/0001-agent-workflow-state.md` — ADR for the agent state-on-disk layout.

### Runbooks (live operations)
- `docs/runbooks/home-assistant-configuration.md` — full workflow for managing HA resources (Workflow A: source-first, Workflow B: UI adoption). Required reading before touching `home-assistant/`.
- `docs/runbooks/jarvis-voice.md`: Jarvis voice signal chain, canonical entity IDs, HA wiring checklist, image pins, and latency notes. Read before touching `gitops/voice/` or `home-assistant/www/jarvis/`.
- `docs/runbooks/roku-bridge-architecture.md` — Roku bulb bridge data flow, PIDs, debug commands, deploy path, and the modern-vs-legacy color schema gotcha. Read before debugging bedroom bulb behavior.
- `docs/runbooks/cloudflare-tunnel.md` — Cloudflare Tunnel operations.
- `docs/runbooks/ci-pipelines.md` — GitLab runner lanes, validation jobs, and path-based routing.
- `docs/runbooks/ship.md` — `just ship` delivery path: PR, required CI, merge, routed deployment operations, promotion, and Flux verification.
- `docs/runbooks/local-development.md` — pinned dev environment, registry commands, and the offline validation gate.
- `docs/runbooks/local-registry.md` — local container registry operations.
- `docs/runbooks/opnsense-recovery.md` — OPNsense disaster recovery.
- `docs/runbooks/opnsense-bgp-proof.md` — OPNsense BGP reachability proof.
- `docs/runbooks/postgres-disaster-recovery.md` — Postgres backup and restore.
- `docs/runbooks/agent-workspaces.md` — multiuser agent workspace operations.
- `docs/runbooks/arr-credentials.md` — media download-chain credential sync (qBittorrent password, *arr/Seerr API keys) and rotation.
- `docs/runbooks/netbird.md` — NetBird dashboard/config API for agents: `NETBIRD_API_KEY` storage in SOPS, read-only usage, and rotation. Required reading before calling the NetBird Management API or changing peers, routes, or policies.
- `docs/runbooks/home-assistant-automation-best-practices.md` — HA automation conventions.
- `docs/runbooks/kiosk-display.md` — shared kiosk module: single-view Jarvis face vs rotating dashboards, and the HA dashboard token seeding.

### Gotchas (code-level pitfalls)
- `docs/gotchas/nix-heredoc-indentation.md` — Nix `''` heredoc indentation stripping trap. Symptom is a runtime `IndentationError` on a service you just edited. `nixfmt` does not catch it.

### Networking
- [Network outage monitor](runbooks/network-monitor.md): local connectivity checks and ntfy outage/recovery alerts.
- `docs/network/opnsense-nat.md` — OPNsense NAT rules.
- `docs/network/switch-port-map.md` — physical switch port layout.

### Plans, designs, and research (dated, for historical context)
- `docs/superpowers/plans/` — dated implementation plans per workstream.
- `docs/superpowers/specs/` — dated design specs per workstream.
- `docs/research/` — dated research notes (k3s, Flux, OPNsense control plane, OPNsense 26.7 API).

### Per-task notes (transient)
- `docs/agent-tasks/*.md` — task-scoped notes exported via `just task-export`. Each one is a snapshot tied to a specific task; check `git log` on the file for context. Persistent task records and runtime evidence live under `.agent-state/`.
