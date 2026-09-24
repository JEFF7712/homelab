# Jarvis Audit Remediation: Audio Endpoint & Chatterbox TTS

Owner: Audio / Host / Chatterbox Agent (Agent 2)
Scope:
- `gitops/voice/satellite.yaml`
- `gitops/voice/satellite/` (`__main__.py`, `satellite.py`, `healthcheck.py`, `__init__.py`)
- `gitops/voice/chatterbox.yaml`
- `gitops/voice/chatterbox-bridge/` (`server.py`, `requirements.txt`, `Dockerfile`)
- `flake/hosts/homelab-05/default.nix`
- `flake/modules/kiosk.nix`
- `tests/test_chatterbox_bridge.py`
- `tests/test_home_assistant_voice_satellite_config.py`
- `tests/test_satellite_recovery.py`
- `tests/test_wake_detector_comparison.py`

Source: `docs/research/2026-09-23-jarvis-voice-audit.md` (audit items B1, B2, B6, B7, B8, A1, A2, A3, O3, O4).

---

## 1. Boundaries & Collaboration

- **Agent 1 (Gateways, STT, Network Policy):**
  - Owns `gitops/voice/gateway/*`, `*-nemotron*`, `whisper*`, `stt-groq*`, `network-policy.yaml`.
  - Fixes network policy scrape timeouts for Chatterbox (port 8001) and preserves port 9100 for Nemotron metrics.
  - Supplies STT/TTS routing and Wyoming protocol-level circuit breakers.
- **Agent 2 (Audio Endpoint, Host, Chatterbox - This Task):**
  - Owns satellite runtime patching, dedicated health endpoint, capture recovery, and playback inhibition.
  - Owns Chatterbox TTS bridge implementation, queue bounding, vectorized PCM, LRU cache, Dockerfile, and cache configuration distinguishing writable scratch from read-only model storage.
  - Owns `homelab-05` host audio and HDMI clock recovery service in NixOS.
  - Coordinates kiosk boot URL (`flake/hosts/homelab-05/default.nix`) with Agent 3's `face_version` when deployed.
- **Agent 3 (Home Assistant, Observability, Evaluation):**
  - Retired `home-assistant/automations/jarvis_satellite_tts_mic_mute.yaml`.
  - Consumes metrics series from `wyoming-tts-chatterbox`:
    - `jarvis_tts_synthesis_success_total`, `jarvis_tts_synthesis_failures_total`
    - `jarvis_tts_time_to_first_audio_seconds_{bucket,sum,count}` (bounded histogram)
    - `jarvis_tts_generated_audio_seconds_{bucket,sum,count}`
    - `jarvis_tts_cpu_fallback`, `jarvis_tts_unwatermarked_audio`
  - Consumes `/healthz` HTTP readiness probe on Chatterbox port 8001.

---

## 2. Remediation Analysis & Implementation Decisions

### 1. Audio Capture Thread Stalling, Dedicated Health Endpoint & Session Ownership (Audit B1)
- **Problem:**
  1. When PipeWire or the USB audio device disconnects or resets, `MicrophoneRecorder` can hang or silently fail to deliver audio frames while `pgrep` reports healthy.
  2. TCP health probes directly connecting to port 6053 constructed `VoiceSatelliteProtocol`, overwriting `state.satellite`, clearing `connected`, and clearing `playback_inhibited`. Probe disconnect then cleared the satellite pointer, breaking active HA voice routing and permitting self-wake during TTS playback.
- **Implementation:**
  - **Dedicated Health Endpoint:** Added an isolated HTTP health endpoint in `gitops/voice/satellite/__main__.py` listening on `127.0.0.1:HEALTH_PORT` (default `10202`). Probing this endpoint checks event loop responsiveness and audio frame freshness without touching the ESPHome protocol or port 6053.
  - **Healthcheck Script:** Updated `gitops/voice/satellite/healthcheck.py` to query `http://127.0.0.1:10202/healthz` and verify `/tmp/satellite_audio_healthy` mtime within 10s. Replaced `pgrep` startup, readiness, and liveness probes in `gitops/voice/satellite.yaml`.
  - **Connection Ownership Protection:** In `gitops/voice/satellite/satellite.py`:
    - `VoiceSatelliteProtocol.__init__` no longer sets `self.state.satellite = self` and no longer resets `self.state.connected` or `self.state.playback_inhibited`.
    - Shared satellite ownership is assigned *strictly* upon an authenticated/established Home Assistant session (`is_established_ha = True`), triggered by `AuthenticationRequest` or `VoiceAssistantConfigurationRequest`.
    - Unauthenticated connections or transient probes that disconnect deregister from `self.state.connections` without modifying `state.satellite`, `state.connected`, `state.playback_inhibited`, or stopping audio players.
  - **Active Watchdog:** Added an internal watchdog in `__main__.py` monitoring frame timestamp freshness (`AUDIO_STALL_TIMEOUT=6.0s`) with bounded exponential backoff (`initial_backoff=1.0s`, `backoff_factor=1.5`, `max_backoff=10.0s`, `max_recovery_attempts=5`) before terminating the container for pod restart.

### 2. Local Playback Inhibition vs Manual Mute (Audit B6, A1)
- **Problem:** An HA automation (`jarvis_satellite_tts_mic_mute.yaml`) previously toggled `switch.homelab_05_satellite_mute` over network RPC during TTS playback. This created network race conditions, left mics muted indefinitely on missed packets, and wiped out manual user mutes.
- **Implementation:**
  - Decoupled playback inhibition from mute state in `gitops/voice/satellite/satellite.py`.
  - Added `self.state.playback_inhibited` as an internal state variable.
  - `self.state.muted` is strictly reserved for user-commanded manual mute and is never mutated during TTS.
  - Configurable acoustic tail settling window via `ACOUSTIC_TAIL_SECONDS` (default `0.35s`).
  - During `play_tts()`, `playback_inhibited` is set to `True`. When playback finishes, a timer holds inhibition for the tail duration before clearing.
  - Manual mute overrides all audio processing; clearing playback inhibition never unmutes a manually muted mic.
  - Agent 3 has deleted the HA network mute automation.

### 3. Turn-Active and Stop-Word Protection (Audit B6, A1)
- **Problem:** Microphone feedback during TTS caused the satellite to trigger on its own speech ("Hey Jarvis" self-wake). Conversely, users could not stop runaway TTS playback.
- **Implementation:**
  - Clarified audio channel topology: Channel 1 of the QuadCast S is a stereo microphone capsule, NOT an acoustic echo cancellation (AEC) reference. WebRTC NS is spectral noise suppression, not echo cancellation. True full-duplex wake detection during loud speaker playback is impossible without an AEC reference loopback.
  - Restricted in-flight barge-in specifically to the dedicated local stop-word detector ("Stop").
  - While `playback_inhibited` is `True`, standard wake words (`hey_jarvis`, etc.) are ignored.
  - If the stop word triggers during TTS playback, `satellite.py` immediately halts media playback via `self.state.tts_player.stop()` and clears inhibition.

### 4. Decoupled HDMI Audio Clock Recovery on homelab-05 (Audit B2)
- **Problem:** `satellite-hdmi-audio-clock.service` previously depended on Chromium's window title matching `"JARVIS"`. If the kiosk crashed, was delayed, or booted with a blank page, HDMI audio recovery failed to initialize, causing audio clock drift and output drops to the LG Soundbar.
- **Implementation:**
  - Decoupled `satellite-hdmi-audio-clock.service` in `flake/hosts/homelab-05/default.nix` from Chromium window title.
  - Service now depends directly on Wayland compositor readiness (`wlr-randr >/dev/null 2>&1`).
  - Added ALSA ELD validation checking `/proc/asound/card*/eld*` for `LG SB`, `eld_valid 1`, and `monitor_present 1`.
  - Added rate-limited recovery with a 15-second cooldown cycle so HDMI modesetting is triggered only when display layout drifts or the audio ELD becomes invalid.
  - Replaced silent `|| true` commands with structured logging to `systemd-cat -t satellite-hdmi-clock`.

### 5. Transient Override Inspection on homelab-05 (Audit B2)
- **Inspection:** Inspected `/run/systemd/system/satellite-hdmi-audio-clock.service.d/pos.conf` and `/run/satellite-hdmi-clock.sh` on `homelab-05` strictly read-only.
- **Findings:** The transient override in `/run` matches the declarative configuration (`pos 0 0` for HDMI-A-2 and DP-3 display arrangements). No unmanaged ad-hoc shell scripts were modified. The declarative NixOS configuration in `flake/hosts/homelab-05/default.nix` is authoritative.

### 6. Bounded Chatterbox Queues and Explicit CPU Fallback (Audit B8, O3)
- **Problem:** Under burst requests or slow synthesis, unbounded request queues caused cascading client timeouts and memory exhaustion. Silent CPU fallback on CUDA errors caused synthesis times to jump from 0.8s to 12s+ without warning.
- **Implementation:**
  - In `gitops/voice/chatterbox-bridge/server.py`:
    - Added bounded queue depth checking (`CHATTERBOX_MAX_QUEUE=2`). Excess concurrent synthesis requests are rejected immediately with a Wyoming `error` event (`text="Chatterbox queue full, rejecting request"`).
    - Added client disconnection detection: if `writer.is_closing()` is true when a turn is dequeued, inference is cancelled before running PyTorch.
    - Explicit CPU fallback policy: If `CHATTERBOX_DEVICE=cuda` and `torch.cuda.is_available()` returns false, the service logs a critical error, raises `RuntimeError`, sets `is_healthy = False`, and fails `/healthz` with 503 unless `CHATTERBOX_ALLOW_CPU_FALLBACK=1` is explicitly set.

### 7. Bounded Histograms & Vectorized PCM Conversion (Audit O4)
- **Problem:** Metrics in `server.py` accumulated unbounded float samples in memory, causing memory bloat and slow Prometheus scrapes. Float-to-int16 PCM conversion used a scalar Python loop over tens of thousands of samples per second.
- **Implementation:**
  - Implemented `samples_to_pcm16` and `normalize_peak` using vectorized NumPy operations with explicit clipping (`np.clip(..., -32768, 32767)`) and rounding (`np.rint()`). Pure-Python fallbacks are provided for environments without NumPy.
  - Implemented fixed-size O(1) Prometheus metrics using standard histogram bucket counters (`HISTOGRAM_BUCKETS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0)`).
  - Added gauges for `jarvis_tts_cpu_fallback` and `jarvis_tts_unwatermarked_audio`.
  - Added HTTP `/healthz` endpoint on port 8001 returning 200 (healthy) or 503 (unhealthy/failed engine).

### 8. Reproducible Chatterbox Dependencies and Model Cache Management (Audit B7, B8)
- **Problem:**
  1. `gitops/voice/chatterbox.yaml` previously copied multi-gigabytes of model weights on every pod startup (`cp -a "$REPO_CACHE" "$RUNTIME_REPO_CACHE"`), doubling disk footprint on the host. Missing Dockerfile and requirements pinned in repo.
  2. `HF_HOME=/models` was mounted `readOnly: true`, but `validate_cache_paths()` required `HF_HOME` to be writable, causing startup validation failure.
- **Implementation:**
  - Created `gitops/voice/chatterbox-bridge/requirements.txt` with pinned dependency versions (`chatterbox-tts==0.1.7`, `torch==2.6.0+cu126`, `torchaudio`, `numpy`, `perth`, `wyoming`).
  - Created `gitops/voice/chatterbox-bridge/Dockerfile` documenting the build recipe.
  - **Distinguished Immutable Storage from Writable Scratch:**
    - Updated `validate_cache_paths()` in `server.py` to distinguish writable runtime scratch caches (`HF_HOME`, `TORCH_HOME`, `TRITON_CACHE_DIR`, `XDG_CACHE_HOME`) requiring `os.W_OK` from immutable model storage (`HF_HUB_CACHE`, `TRANSFORMERS_CACHE`) requiring `os.R_OK`. Read-only model mounts are explicitly supported.
    - Updated `gitops/voice/chatterbox.yaml`:
      - Set `HF_HOME: /runtime/cache/huggingface` (inside writable `/runtime` volume).
      - Set `HF_HUB_CACHE: /models/hub` and `TRANSFORMERS_CACHE: /models/hub` (inside read-only `/models` volume).
      - Set `HF_HUB_OFFLINE: "1"` to ensure no runtime download attempts.
      - Added snapshot validation in init script: checks for `model.safetensors` and `config.json` before skipping redundant downloads.
      - Updated readiness probe to use HTTP `GET /healthz` on port 8001.

### 9. TTS Caching and Streaming Assessment (Audit O3)
- **Analysis:**
  - Home Assistant's `tts` component already maintains an on-disk cache of synthesized audio keyed by text hash and voice parameters. Identical phrases are served directly from HA storage without hitting the Wyoming TTS backend.
  - Added an in-memory LRU cache (`TtsCache`, max 64 items) in `server.py` keyed by `(text, voice)` for direct Wyoming invocations or HA cache misses.
  - **Streaming Assessment:** Chatterbox (FastSpeech2 / HiFi-GAN architecture) generates audio spectrograms globally across the complete phoneme sequence. Chunked intermediate audio streaming before the utterance finishes causes unnatural cadence, prosodic discontinuities, and phase artifacts. True streaming should NOT be faked; bounded batch synthesis with low TTFA (~0.8s) and LRU caching provides the best perceptual quality and latency.

### 10. Wake Detector Evaluation Harness & Pending Acoustic Acceptance (Audit A2, A3)
- **Correction & Status:**
  - **Withdrawn "Optimal" Claims:** Withdrew previous claims that MicroWakeWord / sensitivity 0.5 / NS level 1 was proven optimal from mock RMS math. Simulated energy math cannot evaluate real neural network wake word performance or acoustic behavior.
  - **Real Detector Evaluation Harness:** Implemented `WakeDetectorHarness` in `scripts/jarvis_wake_eval.py` using the installed satellite MicroWakeWord and OpenWakeWord TFLite adapters, their feature extractors, and the production WebRTC processor. It replays labeled WAVs with fresh model state per case; offline adapter tests are not acoustic measurements.
  - **Acoustic Acceptance Explicitly Pending:** Per `tests/acoustic/README.md`, private operator recordings under `tests/acoustic/recordings/` are not yet captured. The harness explicitly marks acoustic acceptance as **PENDING physical capture**. The current satellite configuration (`hey_jarvis.tflite` with NS level 1 and sensitivity 0.5) is retained as the baseline, with empirical optimization deferred until real recordings are captured and replayed.

---

## 3. Verification & Acceptance Results

### Unit Tests
Executed via pinned Nix dev shell (`nix develop ./flake`):
- `tests.test_chatterbox_bridge` (21 tests): **PASS**
  - Vectorized PCM conversion with rounding and clipping.
  - O(1) bounded Prometheus histogram rendering.
  - In-memory LRU TTS cache hit/miss behavior.
  - Queue depth bounding and rejection with Wyoming `error` event.
  - Client disconnection abort during queue wait.
  - Explicit CPU fallback rejection when CUDA requested but unavailable.
  - HTTP `/healthz` status code verification (200 vs 503).
  - Cache validation with actual mount permissions (read-only `/models` and writable `/runtime/cache`).
- `tests.test_home_assistant_voice_satellite_config` (4 tests): **PASS**
  - Volume override preservation (`--mic-volume 75`).
  - Active Python healthcheck script replacing `pgrep` in startup/readiness/liveness probes.
  - ConfigMap subPath mounts for `__main__.py`, `satellite.py`, and `healthcheck.py`.
  - Deployment environment variables (`ACOUSTIC_TAIL_SECONDS`, `AUDIO_STALL_TIMEOUT`, `HEALTH_PORT`).
- `tests.test_satellite_recovery` (7 tests): **PASS**
  - Detection of audio capture stalls via frame timestamp age.
  - Bounded exponential backoff during recorder recovery.
  - Playback inhibition lifecycle and acoustic tail timer.
  - Preservation of user-commanded manual mute across playback events.
  - Stop-word handling and cancellation during playback.
  - Probing while HA is connected and TTS is playing does not corrupt satellite session or clear playback inhibition.
  - Dedicated health endpoint check on loopback HTTP server.
- `tests.test_wake_detector_comparison` (3 tests): **PASS**
  - Acoustic acceptance gate is explicitly marked PENDING in offline/CI environment.
  - Manifest and capture protocol adhere to `tests/acoustic/README.md`.
  - Harness refuses to invent fake synthetic probabilities when real model weights are absent.

**Total:** 35 tests passed in 24.0s.

### Code Style & Formatting
- Python: `ruff format --check` verified clean across all owned files.
- Nix: `nixfmt --check` verified clean on `flake/hosts/homelab-05/default.nix` and `flake/modules/kiosk.nix`.

---

## 4. Handoff & Deployment Instructions

### To Agent 1 & Agent 3
- Agent 3 may verify that `up{job="wyoming-tts-chatterbox"}` and `/healthz` metrics operate on port 8001.
- Agent 1 should ensure the network policy permits Prometheus scraping of `wyoming-tts-chatterbox:8001` and preserves port 9100 for Nemotron.
- When ready to bump the kiosk face, Agent 3 and Agent 2 will synchronize `face_version` in `home-assistant/www/jarvis/config.json` with `flake/hosts/homelab-05/default.nix`.

### Deployment & Rollback Steps (Flux-First Workflow)
1. **Reviewed Git Commit:** All manifest and configuration changes are committed to Git. Direct imperative `kubectl apply` commands are forbidden.
2. **Authorized Flux Reconciliation:** Flux reconciles the `voice` Kustomization directly from the Git repository:
   ```sh
   flux reconcile kustomization voice --with-source
   ```
3. **NixOS Switch:** Apply host configuration changes to `homelab-05`:
   ```sh
   nixos-rebuild switch --flake .#homelab-05
   ```
4. **Rollback Procedure:**
   - For Kubernetes manifests: Git revert the commit and push. Flux will automatically restore previous manifests.
   - For host NixOS: Roll back to previous generation via `nixos-rebuild switch --rollback`.

### Physical Acceptance Testing (Requires Operator at Hardware)
1. **Audio Capture Recovery:** Unplug the USB QuadCast microphone for 10 seconds and reconnect. Verify the pod either recovers internally or restarts via healthcheck probe and resumes capture.
2. **PipeWire Restart:** Run `systemctl --user restart pipewire pipewire-pulse` on `homelab-05`. Verify satellite healthcheck detects the disconnection and cleanly reconnects without pod freeze.
3. **HDMI Clock Recovery:** Switch LG Soundbar input or TV input and return to eARC. Check `journalctl -u satellite-hdmi-audio-clock.service` to verify ELD validity check and mode cycle recovery.
4. **Playback Inhibition & Probing:** Play a loud TTS announcement ("Jarvis, play music") and verify that running `/app/healthcheck.py` during playback does NOT interrupt audio, does NOT clear `playback_inhibited`, and does NOT cause self-wake. Speaking "Stop" halts TTS playback immediately.
5. **Acoustic Corpus Capture:** Record 10-20 WAVs per `tests/acoustic/README.md` to unblock acoustic acceptance evaluation and threshold comparison.
