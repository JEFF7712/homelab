# Jarvis deployment review, 2026-09-27

## Decision

Most audit remediation is already deployed. The remaining functional rollout is the packaged native Nemotron image and its deployment integration. Prepare that as one logical change. The current satellite difference is formatting only and does not justify a restart.

## Current evidence

- Checkout HEAD and Flux `voice` last-applied revision: `71342d56485751b3a17bc8c480ad1eb9741f5514` on `main`.
- Read-only comparison checked 51 rendered voice resources against the API. All desired fields matched except `satellite-patch-code.data.satellite.py`. This comparison checks desired fields; it is not a claim that every server-added field is absent.
- Satellite ConfigMap and mounted runtime files match for all three Python files. The pending `satellite.py` delta consists of two formatting changes. Embedded YAML serialization creates a large textual diff without a corresponding behavior change.
- All 13 Jarvis automations, its dashboard, and `jarvis_play_media` are clean across HA baseline, Git, and live. No HA deployment is needed for those resources.
- HA reports the Jev integration as a UI experiment. Preserve it; adoption is a separate source-ownership decision. The other experiment is the Google conversation integration.
- Live exporter reports `jarvis_exporter_ha_connected 1.0`. This verifies HA connectivity, not successful device actuation.
- Native deployment still uses the Whisper image listed below, with the host-provided dynamic loader and native libraries.
- `homelab-04` reports an NVIDIA T1000 and driver `595.91.07`. The replacement image has passed real model-backed GPU recognition on the laptop RTX 3050; target-node recognition remains a rollout acceptance gate.

Evidence: `.agent-state/evidence/jarvis-deployment-review/{voice-comparison.json,ha-summary.json,satellite-hashes.json,satellite.patch}`. These files contain summaries and code hashes, not credentials.

## Concrete native deployment delta

Owning source: `gitops/voice/stt-nemotron.yaml`.

| Field | Current deployed state | Required rollout change |
| --- | --- | --- |
| Image | `registry.rupan.dev/upstream/docker.io/rhasspy/wyoming-whisper@sha256:ba6fcb6056ebe237d15a325381763a80af2fc8fcedaa04f6a714a7375eb20d80` | Publish the verified native image through the repository registry workflow, read back its registry digest, then pin that immutable reference |
| Command | Host `/opt/nemo/lib64/ld-linux-x86-64.so.2` runs `/usr/src/.venv/bin/python3 /app/bridge.py` | Remove the command override; use the image entrypoint `python3 /app/bridge.py` |
| Native libraries | `nemo-lib` hostPath masks `/opt/nemo/lib64` | Remove that volume and volumeMount so the packaged libraries are used |
| Library search path | Deployment overrides `LD_LIBRARY_PATH=/opt/nemo/lib64` | Remove the override and inherit the image's `/opt/nemo/lib64:/usr/local/cuda/lib64` |
| Python code | Generated `bridge-code` ConfigMap masks `/app` | Remove the `/app` code mount and its volume so the digest owns both bridge and packaged speaker-ID code |
| Preserved runtime inputs | Model, speaker profiles, Whisper speaker-ID cache, GPU allocation, node placement, backend endpoint | Retain these inputs and existing environment settings; validate them against the packaged entrypoint |

Verified local image: `jarvis-nemotron-review:local`, digest `sha256:d9157e0ebd46637f9e0a20864d9deba6105c60cb6192ad4a6506bad3907fc0d6`. This is local build evidence, not a published registry reference. Confirm the producer/publisher contract before publication and use the digest returned by the registry rather than assuming digest identity.

The obsolete generated `nemotron-bridge-code` resource can be removed from `gitops/voice/kustomization.yaml` in the same integration change after its sole consumer is removed. Keep canonical `voice-id/proxy.py` used by the fallback Whisper deployment.

Do not change the image alone: the existing loader, library mount, and `/app` mount would defeat the packaged runtime contract.

## Rollout and acceptance

1. Publish the reviewed image, verify its registry digest and imports, and prepare the complete integration delta above. Run the pinned native/gateway/voice-ID tests and render/schema checks on that delta.
2. Review and publish the logical Git change through the repository workflow, then reconcile Flux `voice`. Publication and reconciliation require authorization because they affect shared infrastructure.
3. Observe the Recreate transition: the primary backend will temporarily be unavailable. Verify the fallback STT backend is protocol-usable beforehand; pod readiness alone is insufficient.
4. Verify real labeled-WAV recognition on the target node through the packaged native runtime, speaker-ID initialization, primary/fallback request outcomes, scrape availability, and exporter HA connectivity. A TCP probe is not native recognition proof.
5. Perform the separately pending physical gates: microphone capture/recovery, self-wake suppression, stop-word interruption, manual mute preservation, HDMI input recovery, and recorded wake comparison. Obtain presence/actuation authorization for device-changing evaluations.

No HA reload is required by this review. Retain the current face/kiosk version unless a separate coordinated source delta requires a cache refresh.

## Rollback

- Record the actual integration commit and published image digest before reconciliation.
- Revert the complete integration commit, including image, command, environment, mounts, volumes, and generator removal. Publish the revert through the same authorized Git workflow and reconcile Flux `voice` with source.
- The known current baseline is `71342d56485751b3a17bc8c480ad1eb9741f5514`; do not revert unrelated commits that arrive afterward.
- Preserve `/persist/nemo-install/lib64` and `/persist/voice-models/nemotron` on `homelab-04`. Keeping the old native libraries permits the previous deployment to run after rollback.
- Verify the previous image and host mounts, backend readiness and a real transcript after rollback. Do not use imperative Kubernetes changes that Flux will overwrite.

## Limits

This review performed read-only cluster/HA inspection and created local review artifacts. It did not publish, reconcile, reload, restart, or actuate devices. Current runtime observations do not replace the remaining target-GPU, physical acoustic, and end-to-end latency acceptance gates.
