# Jarvis Audit Remediation: Gateway, STT, and Network Policy

**Owner:** Gateway / STT / Network Agent (Agent 1)
**Scope:**
- `gitops/voice/gateway/gateway.py`
- `gitops/voice/gateway.yaml`
- `gitops/voice/nemotron-bridge/` (`bridge.py`, `Dockerfile`, `requirements.txt`, `nemo_speech_asr_c.c`, `proxy.py`, `MODEL_CONTRACT.md`, `verify_contract.py`)
- `gitops/voice/stt-nemotron.yaml`
- `gitops/voice/voice-id/` (`proxy.py`, symlink `scripts/voice_id/proxy.py`)
- `gitops/voice/whisper.yaml`
- `gitops/voice/network-policy.yaml`
- `tests/test_voice_gateway.py`
- `tests/test_nemotron_bridge.py`
- `tests/test_voice_id.py`

**Source:** `docs/research/2026-09-23-jarvis-voice-audit.md` (Bugs B1, B2, B3, B7; Optimizations O3; Architectural items A2, A3).

---

## 1. Boundaries and Coordination

- **Shared files (DO NOT TOUCH):** `gitops/voice/kustomization.yaml`, registry locks, and `docs/runbooks/jarvis-voice.md`.
- **Agent 2 (Chatterbox / Host / Satellite):** Owns `chatterbox-bridge/server.py`, `chatterbox.yaml`, `flake/hosts/homelab-05/*`, `gitops/voice/satellite.yaml`. Agent 1 provides ingress network policy for Chatterbox scrape port 8001 and TTS voice mapping `jarvis` -> `en_GB-alan-medium` for Piper while passing `jarvis` intact for Chatterbox.
- **Agent 3 (HA / Observability):** Owns `gitops/observability/`, `home-assistant/`, `scripts/jarvis_*eval.py`, `scripts/voice_topology.py`. Agent 1 emits required metric series with exact label schemas.

---

## 2. Implemented Remediations

### Bug B1: Stable Gateway Identity & Backend Voice Mapping
- **Stable Identity:** `wyoming-tts-gateway` and `wyoming-stt-gateway` advertise consistent Wyoming `info` events when queried with `Describe`:
  - TTS: Single voice `jarvis` (attribution "Jarvis Voice Synthesis", English).
  - STT: Single model `nemotron` (languages `["en"]`).
  - Downstream clients (Home Assistant Assist) see an immutable entity signature, avoiding pipeline reconfiguration when underlying backends switch or fail over.
- **Voice Mapping:** When routing TTS synthesis requests:
  - If target backend is Piper (`wyoming-piper` or port 10200): maps requested voice `jarvis` to installed voice `en_GB-alan-medium`.
  - If target backend is Chatterbox (`wyoming-chatterbox` or port 10201): preserves `jarvis` voice intact.
  - Other explicit voice names are passed through unchanged.

### Bug B2: Voice-ID Prompt Audio-Stop Forwarding & Worker Thread Offload
- **Immediate Event Forwarding:** In `gitops/voice/voice-id/proxy.py`, when `AudioStop` is received from the client, it is forwarded immediately to the upstream Whisper service (`writer_u`). Upstream Whisper can immediately finalize its decoding without waiting for speaker verification.
- **Async Classification Offload:** `classifier.identify_pcm` is executed in a background worker thread via `asyncio.to_thread`.
- **Bounded Coordination:** The upstream-to-client loop awaits the speaker identification result with a 1.5-second timeout upon receiving the `Transcript` event, ensuring zero deadlocks and bounded latency. In-flight classification tasks are tracked and cancelled on new audio turn initialization or client disconnect.

### Bug B3: Outcome-Driven Circuit Breaker & Health Probes
- **Decoupled TCP Probing:** Background TCP probes in `health_loop()` test network connectivity only (`backend.healthy = True/False`). They **never** reset `backend.failures = 0`.
- **Outcome-Based Circuit State:** `backend.record_failure()` is triggered only by actual request timeouts, premature stream EOF, or application errors. The circuit opens for `cooldown_seconds` (default 30s) after consecutive failures exceed `failure_threshold`.
- **Half-Open Single Trial:** Once the cooldown expires, the circuit enters `half_open` allowing exactly one canary request. A successful inference turn calls `backend.record_success()`, resetting failure counters and closing the circuit.
- **Liveness vs. Readiness:**
  - `GET /healthz` and `GET /livez`: Return HTTP 200 as long as the gateway event loop is responsive.
  - `GET /readyz`: Returns HTTP 200 when at least one backend is usable (not circuit-broken and TCP-reachable); returns HTTP 503 if all backends are down/open.
  - Kubernetes readiness probes in `gitops/voice/gateway.yaml` now target `/readyz`.

### Bug B7 & Scrape Policy Coverage: Observability Network Policy
- Updated `gitops/voice/network-policy.yaml`:
  - `allow-prometheus-scrapes` includes `wyoming-chatterbox` alongside gateways, whisper/nemotron, exporter, and local-decision.
  - Permitted scrape ingress ports explicitly cover all active metrics endpoints:
    - TCP 8000: Gateway metrics (`wyoming-gateway`) and Exporter (`jarvis-exporter`).
    - TCP 8001: Chatterbox bridge metrics (`wyoming-chatterbox`).
    - TCP 8080: Local decision metrics (`local-decision`).
    - TCP 9100: Nemotron bridge metrics (`wyoming-stt-nemotron-metrics`).
  - Automated regression test `VoiceNetworkPolicyScrapeCoverageTest` in `tests/test_voice_gateway.py` statically validates policy coverage across all `ServiceMonitor` definitions in `gitops/voice/`.

### Optimization O3: STT Pre-Intent Buffering & Distinct Error Signaling
- **Pre-Intent Audio Buffering:** The STT gateway buffers PCM audio chunks up to 2 MB. If the primary backend (e.g. Nemotron) returns an error, premature EOF, or times out before returning a transcript, the gateway switches to the fallback backend (Whisper) and replays the buffered audio.
- **Distinct Error Signaling:** In `gitops/voice/nemotron-bridge/bridge.py`, native recognition failures (`native_error`) emit an explicit Wyoming `error` event (`code: "recognition_failed"`). Genuine silence or garbage hallucination filtering still returns an empty transcript (`text: ""`).

### Architectural Item A2: Gateway High Availability & Topology Constraints
- In `gitops/voice/gateway.yaml`:
  - Configured `topologySpreadConstraints` with `maxSkew: 1`, `topologyKey: kubernetes.io/hostname`, and `whenUnsatisfiable: ScheduleAnyway` across both `wyoming-stt-gateway` and `wyoming-tts-gateway`.
  - Added `PodDisruptionBudget` resources (`minAvailable: 1`) for both gateways.
  - Purged disabled `groq` backend from STT gateway environment.

### Architectural Item A3: Whisper Host Placement & Migration Plan
- **Current State:** `gitops/voice/whisper.yaml` is pinned to `homelab-04` via `kubernetes.io/hostname: homelab-04` because it mounts host storage at `/persist/voice-models/whisper` (housing `model.onnx` for Voice-ID and Whisper base models).
- **Inventory Audit:** Evaluation of `flake/hosts/` and `HARDWARE.md` confirms that no other cluster node (`homelab-01`, `homelab-02`, `homelab-03`, `homelab-05`) currently has `/persist/voice-models/whisper` provisioned. Migrating Whisper to arbitrary nodes requires either distributing the model weights across hosts via NixOS / Ansible, or backing the storage via a shared PersistentVolume / Ceph / NFS. Per repository rules, current host pinning on `homelab-04` is retained to prevent storage mount failures.

### Nemotron Native Recognizer, Speaker-ID, & Build Packaging
- Packaged `gitops/voice/nemotron-bridge/`:
  - Review correction: the local C substitute emitted fabricated transcripts and has been removed.
  - `Dockerfile` now builds NVIDIA/NeMo-Speech.cpp revision `07003daa7eefea542076310722ccaa89709ee3c3`, packages its dependencies and `proxy.py`, and runs ABI/import validation only.
  - Recognition verification requires an actual model and labeled WAV corpus via `verify_contract.py --manifest`. The earlier claim of real recognition during build is withdrawn.
  - See `MODEL_CONTRACT.md` for the corrected model hash and separate validation gates.

---

## 3. Metrics and Observability Contracts

Exported on `:8000/metrics` by `wyoming-stt-gateway` and `wyoming-tts-gateway`:

| Metric Name | Type | Labels | Description |
|---|---|---|---|
| `jarvis_gateway_backend_available` | Gauge | `backend`, `mode` (`stt`/`tts`) | 1 if backend is usable (circuit closed/half-open and TCP reachable), 0 if down. Driven by actual protocol outcomes. |
| `jarvis_gateway_usable_backends` | Gauge | `mode` | Count of currently usable backends. |
| `jarvis_gateway_active_requests` | Gauge | `mode` | Number of in-flight client connections. |
| `jarvis_gateway_connections_total` | Counter | `backend`, `mode` | Total requests dispatched per backend. |
| `jarvis_gateway_fallbacks_total` | Counter | `from_backend`, `to_backend`, `mode` | Incremented when a primary backend fails and is replayed to fallback. |
| `jarvis_gateway_backend_failures_total` | Counter | `backend`, `mode` | Total execution failures recorded per backend. |
| `jarvis_gateway_stream_failures_total` | Counter | `mode`, `stage` | Failures during client or backend streaming. |
| `jarvis_gateway_rejected_connections_total`| Counter | `mode` | Connections rejected due to no usable backends. |
| `jarvis_tts_time_to_first_audio_seconds` | Histogram | `backend`, `mode` | Latency from TTS request start until first audio chunk received. |
| `jarvis_tts_generated_audio_seconds` | Histogram | `backend`, `mode` | Total duration of synthesized audio produced. |
| `jarvis_tts_synthesis_realtime_factor` | Histogram | `backend`, `mode` | Generation latency divided by synthesized audio length. |
| `jarvis_tts_synthesis_success_total` | Counter | `backend` | Successful synthesis turns. |
| `jarvis_tts_synthesis_failures_total` | Counter | `backend` | Failed synthesis turns. |
| `jarvis_stt_vad_to_final_seconds` | Histogram | `backend`, `mode` | Latency from VAD end to final transcript emission. |

---

## 4. Verification and Test Results

Automated unit tests cover all edge cases, circuit breakers, voice mapping, and async lifecycles:
```sh
python -m unittest tests/test_voice_gateway.py tests/test_nemotron_bridge.py tests/test_voice_id.py
```
- `tests/test_voice_gateway.py`: 19 tests passing (discovery identity, TTS voice rewriting to Piper `en_GB-alan-medium`, Chatterbox preservation, STT fallback on premature EOF/timeout/error, silence handling, circuit breaking & half-open recovery, `/readyz` 503 behavior, client disconnect handling, and static network policy scrape coverage).
- `tests/test_nemotron_bridge.py`: 16 tests passing (audio chunk streaming, error event emission on native decoder failure, silence filtering, packaging source checks, missing artifact rejection, and proxy module synchronization; earlier fake-library tests replaced).
- `tests/test_voice_id.py`: Unit tests passing (async worker thread offload, audio-stop immediate pass-through, task cancellation on audio reset).
- Code formatting and linting: Pinned `ruff format` and `ruff check --select E,F,I,UP --ignore E501` pass cleanly across all owned files.

---

## 5. Deployment and Rollback Procedures (GitOps / Flux Workflow)

All infrastructure and workload mutations follow the repository's GitOps contract backed by Flux. Direct imperative `kubectl apply` mutations are avoided to prevent drift or reconciler overwrites.

### Pre-Deployment Verification
1. Run local test suite:
   ```sh
   python -m unittest tests/test_voice_gateway.py tests/test_nemotron_bridge.py tests/test_voice_id.py
   ```
2. Verify contract and native C ABI:
   ```sh
   python gitops/voice/nemotron-bridge/verify_contract.py
   ```
3. Run repository gates:
   ```sh
   just check-changed
   ```

### Authorized Deployment Sequence
1. Review git diff for scoped changes and ensure no secrets or unintended files are modified:
   ```sh
   git diff --stat
   ```
2. Commit reviewed changes to Git following conventional commit standards.
3. Push to `main` (or merge authorized pull request).
4. Trigger Flux reconciliation to synchronize the cluster from the reviewed Git source:
   ```sh
   flux reconcile kustomization voice --with-source
   ```
5. Verify live rollout status and healthy readiness across the voice namespace:
   ```sh
   kubectl -n voice rollout status deployment/wyoming-stt-gateway
   kubectl -n voice rollout status deployment/wyoming-tts-gateway
   kubectl -n voice rollout status deployment/wyoming-nemotron
   kubectl -n voice get pdb
   ```

### Rollback Procedure
1. Create a Git revert commit for the deployment commit:
   ```sh
   git revert <commit-sha>
   git push origin main
   ```
2. Trigger Flux reconciliation to converge cluster state back to the previous stable Git revision:
   ```sh
   flux reconcile kustomization voice --with-source
   ```
3. Confirm that pods and network policies have restored cleanly without manual kubectl interventions.
