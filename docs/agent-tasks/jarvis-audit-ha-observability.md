# Jarvis audit remediation: HA, observability, evaluation

Owner: HA/observability agent. Scope: `home-assistant/`, `scripts/voice_topology.py`,
`scripts/jarvis_eval.py`, `scripts/jarvis_acoustic_eval.py`,
`gitops/home-assistant/config.yaml`, `gitops/home-assistant/deployment.yaml`,
`gitops/voice/exporter.yaml`, `gitops/observability/kube-prometheus-stack/jarvis-rules.yaml`,
`gitops/observability/grafana/dashboard-jarvis.yaml`, related tests, `tests/acoustic/`.

Source: `docs/research/2026-09-23-jarvis-voice-audit.md` (bugs B4, B6, B9, B10; arch A4; opt O5).

## Boundaries (do not touch)

- Agent 1: gateways, STT, network policies, gateway placement
  (`gitops/voice/gateway/*`, `*-nemotron*`, `whisper*`, `stt-groq*`, `network-policy.yaml`,
  gateway placement). Gateway source is read-only evidence for metric contracts.
- Agent 2: satellite/audio host config, Chatterbox (`flake/hosts/homelab-05/*`,
  `gitops/voice/satellite.yaml`, `chatterbox*`). Chatterbox source is read-only
  evidence. The kiosk boot URL (`flake/hosts/homelab-05/default.nix`) still pins
  `?v=<face_version>`; if `home-assistant/www/jarvis/config.json` bumps
  `face_version`, Agent 2 must bump the kiosk URL in the same deploy or the
  `test_kiosk_boot_url_matches_face_version` gate fails. Face changes here do NOT
  bump the version for exactly this reason; bump is a joint deploy step.
- Shared: `*/kustomization.yaml`, registry locks, `docs/runbooks/jarvis-voice.md`
  (already records the TTS-mute automation as FAILED; retiring the file agrees
  with it, no runbook edit needed).

## Contracts required from other agents

Alert rules added here reference these series; they stay pending (never fire on
absence) until the owning agent ships them:

- Agent 1 (gateway, `wyoming-gateway-metrics` job):
  - `jarvis_gateway_backend_available{backend,mode}` (`mode` in `stt`, `tts`).
    Must be driven by completed protocol outcomes (Agent 1 owns B3 circuit fix);
    consumed by `JarvisSttAllBackendsDown` / `JarvisTtsAllBackendsDown`.
  - `jarvis_tts_time_to_first_audio_seconds_{sum,count}{backend,mode}` emitted on
    the real TTS request path including fallback attempts (audit B7: currently
    returned before TurnMetrics). Consumed by the TTS latency dashboard panels.
  - `jarvis_gateway_stream_failures_total{mode}` /
    `jarvis_gateway_backend_failures_total{...}` for the failure dashboard.
  - `jarvis_stt_vad_to_final_seconds_{sum,count}` for the STT panel (already
    rendered by gateway source; Agent 1 to confirm label set).
  - Chatterbox scrape-timeout network-policy fix (audit B7).
- Agent 2 (chatterbox, `wyoming-tts-chatterbox` job):
  - `jarvis_tts_synthesis_success_total`, `jarvis_tts_synthesis_failures_total`,
    `jarvis_tts_time_to_first_audio_seconds_{sum,count}`,
    `jarvis_tts_generated_audio_seconds_{sum,count}` (all present in
    `chatterbox-bridge/server.py`; Agent 2 owns the O4 bounded-histogram fix).
  - Scrape target `up{job="wyoming-tts-chatterbox"}` must become 1; the
    `JarvisScrapeDown` alert covers it. The Chatterbox scrape-timeout
    network-policy fix (audit B7) is owned by Agent 1; Agent 2 owns only the
    O4 bounded-histogram fix on the bridge itself.

## Decisions

- Volume: spoken numeric levels are always percentages (`level / 100`), clamped
  with `([0.0, x, 1.0] | sort)[1]`, zero preserved via `| float(0.5)` with no
  truthy `default`, invalid input rejected with a spoken error and `stop`
  (never silent 50%). Mute/unmute/skip/volume service calls no longer use
  `continue_on_error` with an unconditional `Done.`. Templates are rendered
  in tests with the real nix-pinned Jinja2 plus HA's `match` test.
- Music: `script.jarvis_play_media` is `mode: queued` (serializes multistep
  search/play/enqueue so a second command cannot interleave a stale
  `replace_next`); returns `accepted` (submitted, unverified) vs `playing`
  (the player is playing media this request queued) vs abort on service
  error. Confirmation compares queue content before/after (plus fresh
  near-start position for same-track replay) with early exit and 1 s
  polls, so stale playback never verifies and pre-started playback adds
  no wait. The playback automation replies `Done.` for accepted-or-playing
  (documented as request acceptance, not verified playback),
  `Could not start playback.` otherwise.
- TTS mute automation retired (file deleted). Manual mute intent is preserved by
  doing nothing: no automation touches `switch.homelab_05_satellite_mute`.
  Satellite-local playback inhibition is Agent 2's surface (audit B6/A1).
- Jev fallback: empty/unknown/self stays disabled with `general_disabled`;
  self-aliases resolved via config entries + entity registry; one in-flight
  delegation per conversation (bound depth 1, no re-entrant recursion);
  delegation wrapped in `FALLBACK_TIMEOUT_SECONDS`. General conversation stays
  off (`OLLAMA_FALLBACK_AGENT = ""`) until separately configured.
- Topology: no more `core.config_entries` storage scraping. Live pipeline data
  comes from the supported `assist_pipeline/pipeline/list` websocket API plus
  `get_states` entity existence; without credentials the report says `unknown`
  with an explicit reason. The selected pipeline is reported only from the
  satellite entity's own selection attribute; otherwise selection is
  `unknown` (the preferred pipeline is named as evidence, never substituted).
  Stale integrations are flagged, never mutated.
- Exporter: service calls count as tool calls only on positive correlation
  (call context id equals the turn context id, or its parent does for
  automation-triggered actions); anything else during processing, including
  contextless calls, increments `jarvis_unmatched_service_calls_total`.
  `jarvis_requests_total` documents response-phase observation, not
  audible/physical success; per-turn action presence is now
  `jarvis_turns_with_action_total`.
- Face: `subscribe_entities` for exactly the 3 displayed entities; change
  events merged per HA 2026.9.1 `messages._state_diff_event`
  (`{"c": {entity: {"+": {...}, "-": {...}}}}`; bare shapes ignored);
  heartbeat ping 30 s, stale watchdog 90 s, backoff capped at 30 s with
  jitter. Node harness drives the real bundle against a live-captured
  snapshot plus exact-shape diffs. `face_version` NOT bumped here; needs
  joint bump with Agent 2's kiosk URL.
- Acoustic: no private recordings exist (`tests/acoustic/recordings/` absent,
  gitignored; only unrelated `/tmp/test-mic.wav` on this host). Harness +
  manifest validation ship; capture protocol documented in
  `tests/acoustic/README.md`; physical recording gate stays open (see below).

## Remaining gates (not closable from this scope)

- Acoustic recordings (10-20 WAVs per protocol) + replay runs against each STT
  backend + wake/AEC measurements during loud playback: needs physical access.
- Live intent evaluations that actuate devices: forbidden by task constraints;
  `scripts/jarvis_eval.py` intent-stage runs remain manual when someone is home.
- Gateway/Chatterbox metric repairs + dashboard panels depending on them.
- Joint face_version + kiosk URL bump deploy.
- Deployment itself + HA reload: explicitly out of scope; rollback is Flux
  revert of the commit (stateless manifests; HA automations re-applied via
  `just ha-apply` after `just ha-diff` review; exporter/face roll back with
  the Deployment/ConfigMap).

## Verification

- `nix develop ./flake -c python -m unittest <owned test modules>`
- `just ha-validate` (+ `--sync-gitops` after component/sentence edits) and
  careful diff review of `gitops/home-assistant/config.yaml` + checksum.
- `just fmt-check`, `just check-changed`.
- Skipped acoustic/dependency tests are recorded as explicit limitations.
