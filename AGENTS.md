# Repository Guidelines

## Agent Operating Contract

Complete authorized work through verification. Inspect `git status`, applicable instructions, and affected code first. Preserve unrelated changes; multiple agents often work concurrently, so isolate tasks in dedicated Git worktrees (`.worktrees/` is git-ignored) to avoid dirty checkout, branch, or index collisions. Define observable acceptance criteria before editing; plan only multistep work. Resolve routine choices autonomously. Explain material architecture tradeoffs before implementation. Use the standing delivery authorization below for requested work.

**Standing delivery authorization.** A request to make, fix, add, or deploy a change authorizes implementation, validation, push as `homelab-agent`, machine-managed PR and merge, application and infrastructure deployment, live verification, and necessary recovery. Use `just ship`; include task-specific acceptance commands with `--verify` when applicable. No code review or separate operator approval is required, including for CI policy, credentials, networking, and storage work within the requested scope. Do not infer unrelated destructive changes from a service installation; verify backups and recovery before intended persistent-data destruction. Resolve merge conflicts, failed checks and recoverable deployment errors and continue autonomously. Ask only for unavoidable account/UI steps or missing information that prevents completing the task. Resume merged work with `just ship --resume <merge-sha> [--deploy <ops>]`. For necessary direct live mutations within the task, carry `HOMELAB_AUTHORIZED=1` for the mutation guard; no further confirmation is needed. Never bypass branch protection or hooks. During the policy transition, the previously deployed policy can still file a passkey request; this is a one-time UI migration step, not permission to bypass its enforcement.

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

Agent workspaces share about 8 GiB of RAM across every concurrent session, and one full `repository-contract` build peaks near 5 GiB. Validate locally with `just check-changed` and focused `python -m unittest` modules; leave `just check` and full flake check builds to CI. To diagnose a CI failure, read the pipeline log or run only the failing test modules. Close idle agent sessions; each holds about 300 MB.

## Documentation Index

Read narrowly. `AGENT_MAP.md` maps each concern to its owning files, checks, and runbook; `just diag <app>` names the runbooks for a live app. The full index of runbooks, gotchas, and historical plans is [docs/README.md](docs/README.md). Runbooks in `docs/runbooks/` are the authoritative procedure for live operations; plans under `docs/superpowers/` are dated records, not active state.

## Collaboration and Completion

When multiple agents work concurrently, isolate tasks in dedicated Git worktrees instead of modifying a shared working tree with unrelated changes. When delegating, assign disjoint file ownership and acceptance criteria; integrate serially and verify combined results. Keep commits atomic with imperative, scoped subjects. Never bypass hooks. Review the final diff for accidental changes and secrets.

Handoffs and merge requests state changes, verification results, and limitations. Verify delegated work independently.

## Infrastructure Changes

Keep secrets SOPS-encrypted and preserve TLS verification. Before deployment, verify target identity, review the plan, and identify recovery. Use CI for OpenTofu and OPNsense changes and Flux for Kubernetes. Verify live state after authorized deployment.
