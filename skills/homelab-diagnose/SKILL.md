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
- Jellyfin iOS `AVPlayerItem -12889 CoreMediaErrorDomain` at 0ms with a healthy server means the native AVPlayer rejected the direct-streamed audio (FLAC/DTS/TrueHD in HLS). Fix client-side: disable "use native media player", pick an AC3 track, or lower the bitrate to force an AAC transcode.
- Jellyfin OOM (exit 137) after enabling ASS subs is stacked burn-in transcodes: each retry spawns a full QSV transcode at source bitrate (`RemoteClientBitrateLimit` 0 means uncapped, observed 59 Mbps). Mitigate with a global internet bitrate cap plus SRT subs; the cap shrinks sessions but does not limit session count, so no retry-spam.
- Jellyfin behind the Cloudflare Tunnel reports `IsInLocalNetwork: True` with `RemoteIP` = the cloudflared pod when `KnownProxies`/`LocalNetworkSubnets` are empty, so the remote bitrate cap never applies and tunnel viewers get uncapped 4K transcodes. Fix: `KnownProxies` to pod/service CIDRs, `LocalNetworkSubnets` to LAN only, in `/config/network.xml` on the PVC, then restart. Note the PVC config survives Flux reverts, but avoid `kubectl rollout restart` on Flux-managed workloads: the next reconcile strips the restart annotation and rolls the ReplicaSet back, causing a second disruptive restart mid-stream.
- `loki-chunks-cache` requests look alarming (observed 9.8Gi) but are requests, not usage; check actual usage before resizing.
- Registry imports: the GitLab importer lane can be dead twice over (DR-gated `CI_DR_ACTIVE`, stale runners) and its stored passwords can diverge from the NAS htpasswd. Probe a credential first: 404 means auth works and the repo is absent, 401 means the credential is stale. Rotate the htpasswd entry on nas-01 and sync the GitLab variable, per `docs/runbooks/local-registry.md`.
- Node-shell curl to a remote pod IP or VIP proves nothing: host-netns traffic to remote pods hangs even on healthy clusters (BPF CT entry created, nothing forwarded, no drop event). Probe from a tmp pod on the same node (`kubectl run`) or from a real client instead.
- A Cilium LB VIP serves only the Service port (jellyfin 80, not backend 8096), and LAN clients need a DHCP-provided route to 10.0.40.0/24; check `ip route get` on the client before blaming the cluster.
- In multi-node Cilium with `externalTrafficPolicy: Cluster`, traffic hitting node A for a backend on node B gets SNAT'd to node A's router IP (`10.42.0.x`) with identity `world`. Any ingress NetworkPolicy on the backend pod allowing only private client CIDRs (`10.0.0.0/16`) will drop the forwarded SYN on node B (`Policy denied` in `bpf_lxc.c`).

## Reply shape

Per-service verdict: healthy / degraded (with cause) / unknown (with what blocked the read). A failed live read is `unknown`, never evidence of deletion. End with the single most likely cause and the proposed fix as a separate step, not applied.

## Maintaining this skill

This file is a living doc. When triage teaches you something durable (a new gotcha, a changed command, a dead step), edit this file in the same session: add the gotcha under "Known gotchas" or fix the ladder, keeping entries to one or two lines each. Do not let it grow into a runbook; procedures belong in `docs/runbooks/`, this file holds the triage path only. Verify doc edits with `just fmt-check`.
