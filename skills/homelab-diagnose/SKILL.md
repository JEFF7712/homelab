---
name: homelab-diagnose
description: Triage a live homelab problem. Use when the user reports something is down, asks if the cluster is healthy, wants errors analyzed, or asks for a next-day recheck. Read-only until a fix is agreed.
---

# Homelab Diagnose

Triage ladder for the k3s cluster (Flux-reconciled, `gitops/clusters/homelab-01/`). Diagnose fully before proposing any fix. Do not edit manifests as part of triage.

## Ladder

1. `just status cluster` for the bounded live overview.
2. Flux sync state: check the affected app under `gitops/clusters/homelab-01/` and whether it reconciled cleanly. A stuck HelmRelease or failed Kustomization is the cause more often than the workload itself.
3. Pod state: `kubectl` events and logs for the failing workload's namespace. Read events before logs; CrashLoopBackOff with no log output usually means probes or scheduling, not app code.
4. If a HelmRelease failed to roll out: read the release status and the failing pod's events, not the chart values first.
5. Re-run `just status cluster` after any fix to close the loop, and say so in the reply.

## Known gotchas (do not re-learn these)

- Tunnel origins must use in-cluster Service DNS names. Never point a tunnel origin at a Cilium LB VIP (`10.0.40.x`): Cilium implements it as a local redirect that only answers in host network namespaces, so `cloudflared` in pod netns blackholes it. See `docs/runbooks/cloudflare-tunnel.md`.
- Never raw-TCP-probe the voice satellite port 6053: `linux-voice-assistant` uses the single-client ESPHome native protocol, and any unauthenticated probe overwrites satellite state and breaks the HA connection on close. Liveness is a `pgrep` exec check by design. See `docs/runbooks/jarvis-voice.md` ("Satellite health").
- Stale admission-error pods and failed `HelmRelease` rollouts (observed on Grafana) are usually Flux ordering or webhook issues, not chart bugs.
- `loki-chunks-cache` requests look alarming (observed 9.8Gi) but are requests, not usage; check actual usage before resizing.

## Reply shape

Per-service verdict: healthy / degraded (with cause) / unknown (with what blocked the read). A failed live read is `unknown`, never evidence of deletion. End with the single most likely cause and the proposed fix as a separate step, not applied.

## Maintaining this skill

This file is a living doc. When triage teaches you something durable (a new gotcha, a changed command, a dead step), edit this file in the same session: add the gotcha under "Known gotchas" or fix the ladder, keeping entries to one or two lines each. Do not let it grow into a runbook; procedures belong in `docs/runbooks/`, this file holds the triage path only. Verify doc edits with `just fmt-check`.
