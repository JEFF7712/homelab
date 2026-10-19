# Repository Guidelines

## Agent Operating Contract

Complete authorized work through verification. Inspect `git status`, applicable instructions, and affected code first. Preserve unrelated changes; multiple agents often work concurrently, so isolate tasks in dedicated Git worktrees (`.worktrees/` is git-ignored) to avoid dirty checkout, branch, or index collisions. Define observable acceptance criteria before editing; plan only multistep work. Resolve routine choices autonomously. Explain material architecture tradeoffs before implementation. Obtain authorization before publishing or changing shared infrastructure, except under the standing authorization below.

**Standing delivery authorization.** When the user asks for a change to be made, fixed, added, or deployed, that request authorizes delivering it with `just ship`: pushing the branch as `homelab-agent`, opening the PR, merging once `validation-v2` is green, running the routes `ship` marks automatic (application images, Home Assistant), and shipping their promotion PRs. Do not stop to ask between these steps. Infrastructure routes that `ship` holds (fleet, agent-workspace host, OPNsense, Cloudflare) and any operation outside `config/ci/ship-routes.json` still need explicit authorization; once granted, pass them with `just ship --deploy <ops>`. Never bypass branch protection or hooks.

## Workflow Entry Points

Start with `just agent-context`, then use `AGENT_MAP.md` to find the owning source. When multiple agents or tasks are in progress, create an isolated worktree (`git worktree add .worktrees/<id> -b <branch>`) so branch switching, uncommitted changes, and verification fingerprints do not collide. Deliver finished work with `just ship` (see [docs/runbooks/ship.md](docs/runbooks/ship.md)). Track work with `just task-new <id>`, `just task-resume <id>`, `just task-checkpoint <id>`, and `just task-export <id>`. Use `just check-changed` for scoped validation, `just check` for the full offline gate, and `just fmt-check` before handoff. Diagnose the local setup with `just doctor`. Run live diagnostics explicitly with `just status cluster` or `just status network`.

Launch fresh clients with `just agent-run <id> <client>` to inherit task selection. Supported clients and native plugin setup are listed in `docs/agent-workflow.md`. Use `just agent-verify --json --record --task <id> -- <command>` to record source-bound verification. Task records and evidence under `.agent-state/` are local and ignored. Export reviewed handoffs with `just task-export <id>` and transfer uncommitted patches separately. Do not auto-stage files. Full command contracts and schemas are in `docs/agent-workflow.md`.

## Context and Ownership

Read narrowly: start with `README.md`, then affected modules, tests, and runbooks. Treat plans as intent, source as implementation, and fresh observations as runtime evidence.

- `flake/`: hosts, reusable modules, disks, networking, k3s.
- `tofu/opnsense/`: provider-supported firewall resources.
- `opnsense_reconciler/`: inventory and settings outside provider coverage.
- `gitops/`: Kubernetes desired state, owned by Flux.
- `home-assistant/` and `scripts/home_assistant/`: Home Assistant source-first configuration.
- `registry/` and `scripts/registry/`: local container registry supply contract.
- `config/agent-workspaces/`, `scripts/agent_workspaces/`, and `flake/modules/agent-workspace*.nix`: multiuser agent workspaces.
- `scripts/agent/`, `hooks/`, `.opencode/plugins/`, and client adapters (`.claude/`, `.codex/`, `.cursor/`, `opencode.json`): agent workflow tooling.
- `3d-prints/`: local-only CAD sources and generated mounts (git-ignored, excluded from checks).
- `tests/`: checks; `docs/`: architecture and runbooks.
- `secrets/`: encrypted material; `HARDWARE.md`: hardware inventory only.

Give each resource one authoritative owner. Fix the owning source. Put specialized instructions near their subsystem; keep this file small and avoid duplicating documentation.

## Implementation Standards

Prefer explicit interfaces, cohesive modules, and deterministic behavior. Separate pure reconciliation decisions from I/O. Make mutations idempotent, validate before writes, and report actionable failures. Avoid speculative abstractions and compatibility layers without callers.

Use `nixfmt`, two-space YAML indentation, and typed Python with four-space indentation. Prefer automated enforcement over prose rules. Update affected documentation with behavior changes.

## Verification Contract

Use the pinned environment (`nix develop ./flake`). The canonical gates are:

```sh
just check-changed  # scoped validation selected from changed paths
just check          # full offline gate, defined by scripts/checks/all.sh
just fmt-check      # formatting gate before handoff
```

Format changed Nix files with `nixfmt`. Test observable behavior and failure paths in `test_*.py` files. Add meaningful regression tests for bugs. Prefer evaluated configuration and rendered manifests over source-string assertions. Keep routine tests offline and credential-free. Match verification to risk; static success does not prove deployment. Report missing checks explicitly.

## Documentation Index

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
- `docs/network/opnsense-nat.md` — OPNsense NAT rules.
- `docs/network/switch-port-map.md` — physical switch port layout.

### Plans, designs, and research (dated, for historical context)
- `docs/superpowers/plans/` — dated implementation plans per workstream.
- `docs/superpowers/specs/` — dated design specs per workstream.
- `docs/research/` — dated research notes (k3s, Flux, OPNsense control plane, OPNsense 26.7 API).

### Per-task notes (transient)
- `docs/agent-tasks/*.md` — task-scoped notes exported via `just task-export`. Each one is a snapshot tied to a specific task; check `git log` on the file for context. Persistent task records and runtime evidence live under `.agent-state/`.

## Collaboration and Completion

When multiple agents work concurrently, isolate tasks in dedicated Git worktrees instead of modifying a shared working tree with unrelated changes. When delegating, assign disjoint file ownership and acceptance criteria; integrate serially and verify combined results. Keep commits atomic with imperative, scoped subjects. Never bypass hooks. Review the final diff for accidental changes and secrets.

Handoffs and merge requests state changes, verification results, and limitations. Verify delegated work independently.

## Infrastructure Changes

Keep secrets SOPS-encrypted and preserve TLS verification. Before deployment, verify target identity, review the plan, and identify recovery. Use CI for OpenTofu and OPNsense changes and Flux for Kubernetes. Verify live state after authorized deployment.
