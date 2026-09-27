# Jarvis native rollout, 2026-09-27

## Result

Published the reviewed native Nemotron image and deployed the complete packaged
runtime integration through Git and Flux. Target-node recorded-speech recognition,
silence handling, stable gateway routing, and fallback recognition passed.

- Publisher preparation commit: `205f7d0`.
- Native integration commit and rollback target: `23956c19600ae1b3f8319673a776289e7460034a`.
- Published reference: `registry.rupan.dev/apps/jarvis-nemotron@sha256:3e26b17743cd934ea7b9aea6b3d2c7fc04c809a75f223d60bf2b2fad835fe8fc`.
- Native pod: `wyoming-nemotron-65cfd79974-hp97b`, `homelab-04`, Ready 1/1 with zero restarts at verification.
- Flux `voice`: Ready at the integration revision.

## Registry and integration

Protected CI job `16767038091` provisioned `publisher-jarvis-nemotron` with
read/create/update access only to `apps/jarvis-nemotron`. Existing policy was
compared against the lock before mutation. An attempted write to an unrelated
app repository returned HTTP 403. The protected htpasswd file variable was
synchronized with runtime state.

The verified local artifact was copied with that scoped identity. The remote
manifest digest was read back and checked against its bytes and registry header;
the entrypoint and non-root user were verified from its config. A temporary
process-local resolver override selected the private TLS endpoint, preserving
hostname verification and the laptop's global DNS configuration.

The deployment now inherits the packaged entrypoint and library search path.
Host native-library and `/app` code mounts were removed, along with their
volumes and obsolete ConfigMap generator. Model weights remain on homelab-04
and are mounted read-only. Speaker profiles, speaker-ID cache, endpoints,
placement and resource allocation were preserved.

## Verification

| Check | Observed result |
| --- | --- |
| Focused gateway/native/speaker-ID/transcript tests | 61 run: 51 passed, 10 skipped for missing dependencies or local data |
| Packaged deployment regression | Passed |
| Publisher credential/policy tests | 2 passed locally and in protected CI |
| Registry policy/copy-plan gate | Passed before publication preparation and final integration |
| Rendered voice manifests | 50 resources, 41 schema-valid, 9 unchanged CRDs skipped, zero errors |
| Formatting, lint and Git diff whitespace | Passed on changed source |
| GitLab CI configuration lint | Valid |
| Target native ABI/import contract | All 13 symbols and packaged speaker-ID import passed |
| Recorded JFK WAV through local Whisper fallback | Expected transcript, 1.044 seconds |
| Same WAV directly through native backend | Expected transcript, 0.991 seconds |
| Same WAV through stable STT gateway | Expected transcript, 0.532 seconds |
| Two seconds of synthetic silence through native backend | Empty transcript, 0.155 seconds |
| Speaker-ID startup | Both existing profiles loaded; recorded test speaker remained unknown |
| GPU runtime | Native startup reports CUDA0; T1000 memory used 980 MiB after recognition |
| Both STT and both TTS gateway replicas | All `/readyz` checks passed |
| Prometheus | All seven active voice scrape targets report `up=1`, including the new native pod |
| Exporter | `jarvis_exporter_ha_connected 1.0` after rollout |

Probe durations are batch replay times for the recorded file, not microphone-to-response
latency benchmarks. No recorded clip was passed to HA intent processing or device
actuation. Physical audio acceptance remains separate.

The full integration pipeline `2887652056` was still queued/running at the final
check, with registry, repository, schema and secret-scan jobs pending. The
protected publisher job and the scoped local gates above completed successfully.

## Remaining limitation

The existing GGUF predates its embedded SentencePiece tokenizer. Native logs
explicitly report that RNNT word boosting is disabled because the configured
phrases cannot be tokenized. The retained `NEMO_BOOST` configuration does not
provide phrase biasing with these weights. Restoring biasing requires a separately
validated GGUF export with the matching tokenizer; native recognition itself
passed. No model weights were replaced during this rollout.

## Rollback

Revert integration commit `23956c19600ae1b3f8319673a776289e7460034a`, publish
the revert and reconcile Flux `voice` with source. Revert the image, loader,
environment, mounts, volumes and generator as one unit. The previous host-native
library directory and model weights were preserved. Verify a real transcript
after rollback. Keep the publisher identity and published image available until
acceptance and rollback retention are complete.

Protected registry provisioning rollback material is on the NAS at
`/persist/zot/jarvis-publisher-1790551590064636352`. It contains previous
htpasswd and ACL files. Reverting the native deployment does not require undoing
registry provisioning.

## Evidence and scope

Evidence is under `.agent-state/evidence/jarvis-native-rollout/`: publication,
remote manifest and headers, pre-rollout state, Flux/rollout logs, native startup,
recorded/silence probes, gateway readiness and scrape health summaries.

The workspace was fast-forwarded to the integration commit while preserving
unrelated dirty files. An unpublished mixed commit caused by concurrent staging
was replaced before publication with a four-file registry commit; the other
staged work was preserved. Subsequent native integration used an isolated checkout.
No HA reload, physical audio test or device actuation was performed.
