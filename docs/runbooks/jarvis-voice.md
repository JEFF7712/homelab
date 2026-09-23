# Jarvis voice runbook

## Scope and ownership

Jarvis is the Homelab voice assistant. The container stack is owned by Flux
(`gitops/voice/`, deployed via `gitops/clusters/homelab-01/voice.yaml`). The
kiosk face is owned by NixOS (`flake/hosts/homelab-05/default.nix` plus
`flake/modules/kiosk.nix`) and served as HA local media
(`home-assistant/www/jarvis/`). The HA-side routing (Wyoming servers, Assist
pipeline, conversation agent, exposed entities) is UI-managed config-entry
state, which this repo treats as `observe-only`; the required settings are
recorded here so a rebuild restores them exactly.

## Signal chain

Microphone on `homelab-05` (`HyperX QuadCast S` via Pipewire, pinned with
top priority 3500 in WirePlumber, above sink monitors at 100) -> satellite
container (`linux-voice-assistant`,
wake word `hey_jarvis.tflite`, port 6053, device substring match `QuadCast`)
-> Home Assistant on `homelab-03` -> stable STT gateway
(`wyoming-whisper.voice:10300`) -> Nemotron primary, local Whisper fallback,
and optional Groq fallback -> transcript with an optional `speaker <Name>` tag.
TTS uses the stable `wyoming-chatterbox.voice:10201` gateway with Chatterbox
Turbo primary and Piper fallback. Backend preference and circuit state belong to
the gateways, so HA pipeline identifiers do not change during backend failover.
There is no local LLM: Qwen/Ollama was removed in Phase 0 to free the
T1000 GPU, and the cloud replacement is not wired yet.
-> audio back to the `homelab-05` soundbar (HDMI-A-2 with continuous video clocking daemon,
prioritized at 3000 in WirePlumber) -> face state via the HA websocket.

## Canonical entity IDs

Do not rename these without updating `home-assistant/www/jarvis/config.json`,
`home-assistant/www/jarvis/app.js` defaults, and
`home-assistant/dashboards/lovelace-jarvis.yaml` together:

- `assist_satellite.homelab_05_satellite_assist_satellite`
- `switch.homelab_05_satellite_mute`
- `media_player.homelab_05_satellite_media_player_2`
- `switch.homelab_05_satellite_thinking_sound`
- `number.homelab_05_satellite_mic_volume`
- `select.homelab_05_satellite_wake_word`

`CLIENT_NAME` in `gitops/voice/satellite.yaml` must stay
`Homelab 05 Satellite`. HA derives the entity-ID prefix from it; renaming the
client renames every entity above and breaks the face, the dashboard, and any
automation referencing them.

## HA wiring checklist (UI, observe-only)

1. Wyoming integrations pointing at the in-cluster services:
   `voice-satellite.voice:6053` is the satellite link from HA;
   `wyoming-whisper.voice:10300` for stable STT and
   `wyoming-chatterbox.voice:10201` for stable TTS. HA never points directly at
   a backend Service. Gateway preference order owns routine backend failover.
2. One Assist pipeline with the stable STT gateway and Chatterbox `jarvis`
   through the stable TTS gateway,
   and the `Jarvis Jev Router` custom
   conversation agent as the brain. There is no local LLM since Phase 0
   (`gitops/voice/ollama.yaml` deleted, Qwen removed to free the T1000).
   General conversation replies `General conversation is unavailable`
   until a cloud fallback agent is configured. Set it as the preferred
   pipeline and select it on the
   satellite device.
   The Jarvis conversation prompt file carries the room semantics
   (`home-assistant/conversation/jarvis_prompt.md`, kept in Git for the
   future cloud agent):
   downstairs means Living Room plus Kitchen, light commands with no room
   default to all downstairs lights, light color changes default to
   Govee light bulbs, and music/artist/playlist requests explicitly call
   `script.jarvis_play_media` (`media_content_type` varies: `artist`/`track`/`album`/`playlist`). The canonical
   prompt source is `home-assistant/conversation/jarvis_prompt.md`
   ("Conversation prompt" below); the live copy is UI-managed config-entry
   state, so keep the two in sync on any edit.
   Required conversation-agent settings, in priority order:
   - `Prefer handling commands locally`: ON. Built-in intents answer basic
     light, scene, and state commands without reaching the conversation
     agent. The conversation agent is the fallback for ambiguous,
     multi-step, music, and general questions (currently unavailable
     without a cloud fallback).
   - `Max history messages`: 4. Applies to the future cloud agent;
     follow-ups inside one continued conversation still work.
   - `llm_hass_api`: `assist` only once a cloud agent lands (omitting
     SmartHQ saves ~1.2k tokens of unused tool schemas).
   The `light.downstairs_lights` group in
   `home-assistant/core/configuration.yaml` is the deterministic backup for
   the room semantics.
3. Terse local replies are enforced in Git, not in the conversation-agent
   prompt: a local
   command never reaches the conversation agent, so the prompt cannot shorten it.
   `home-assistant/custom_sentences/en/jarvis_terse.yaml` overrides the
   built-in action-intent responses (`HassTurnOn`, `HassTurnOff`,
   `HassToggle`, `HassLightSet`) with `Done.` It deploys via the
   `home-assistant-config` ConfigMap
   (`scripts/home_assistant/adapters/core.py` renders every
   `home-assistant/custom_sentences/<lang>/*.yaml` into it under dotted
   `<lang>.<name>` keys, since ConfigMap keys cannot contain slashes, and
   the HA initContainer splits them back into `/config/custom_sentences/`
   on boot). State queries keep their
   dynamic built-in responses; do not override them with static text.
   Extend the override file only after inspecting Assist traces for the
   next most common intents.
   Signature colors live one layer up: the built-in `HassLightSet` handling
   accepts color *names* only (validated by `color_name_to_rgb`), so the
   conversation agent can never emit exact hex and "pink" always lands on CSS pink
   (255, 192, 203). `home-assistant/custom_sentences/en/jarvis_colors.yaml`
   maps phrases like "neon pink" / "hot pink" onto room and plug targets,
   and `intent_script.JarvisSignatureColor` in `configuration.yaml` fires
   `light.turn_on` with hardcoded `rgb_color: [255, 16, 240]`, replying
   `Done.` To add a color: append its phrases to the `jarvis_color` list
   and add a `choose` branch on `{{ jarvis_color }}` in the intent script.
   These requests resolve in the local intent engine, never reaching the
   conversation agent.
4. Expose 15 to 25 conceptual controls to the future cloud conversation
   agent, not every raw
   entity. Prefer groups (`light.downstairs_lights`), named lamps actually
   mentioned by voice, climate controls, scenes, music, and the shopping
   list. Diagnostic and status sensors stay available to deterministic local
   queries but do not go into the conversation agent tool schema. Exposure audit 2026-09-17:
   59 down to 37, then cut to 19 managed controls (5 light groups, 4
   plug/sign singles, thermostat plus 2 AC sensors, 3 shared scenes,
   `script.jarvis_play_media`, shopping list, satellite media player,
   stairs light). Dropped: 7 Govee/Roku singles covered by their groups,
   6 bedroom scenes, AC power sensor. Four unregistered entities (sun,
   zone, `sensor.ha_latest_version`, `conversation.home_assistant`) have no
   exposure flag and stay visible; negligible cost.
   Deliberately unexposed: all AC alert internals, Bambu bed/nozzle
   thermometers, AC RSSI/energy-counter sensors, browser_mod screen and
   player, the AC temperature-units selector, the four adaptive-lighting
   control switches (name-collision risk with "turn off all lights"), and
   `switch.zigbee2mqtt_bridge_permit_join` (voice must never open Zigbee
   pairing). Kept AC ambient-temperature, power, and mode sensors for
   "how warm / is it on" queries. When adding devices, expose only the
   control entity, never diagnostic sensors.
5. The kiosk bypass in `configuration.yaml` `trusted_networks` must keep
   `10.0.30.15/32` so an interactive HA login from the kiosk stays
   passwordless. The face websocket itself always needs a long-lived
   token: `homelab.kiosk.haTokenFile`
   (`flake/modules/kiosk.nix`, set to
   `/persist/secrets/jarvis-kiosk-ha-token` on `homelab-05`) holds it,
   and `kiosk-ha-token-seed.service` re-seeds it into the kiosk
   browser over its local DevTools port on every boot. The token lives
   only in that host file and the browser profile, never in Git.
   To rotate: create a new long-lived token in the HA UI and overwrite
   `/persist/secrets/jarvis-kiosk-ha-token` (root, mode 600) on
   `homelab-05`, then reboot or restart `kiosk-ha-token-seed.service`.
6. Satellite audio and VAD tuning (on device `Homelab 05 Satellite`):
   - `select.homelab_05_satellite_finished_speaking_detection`: set to `aggressive`
     (0.25s silence detection vs. 0.7s default).
   - `select.homelab_05_satellite_mic_noise_suppression`: set to `High` (level 3).
   - `number.homelab_05_satellite_mic_volume`: set to `75.0`. At 100% gain, the
     QuadCast S condenser mic saturates room ambient noise, causing Silero VAD to
     treat background hum as ongoing speech until hitting the 10-second STT
     safety timeout. Lowering to 75% with High noise suppression detects the end
     of speech immediately.
7. Music playback (Music Assistant & providers):
   - `script.jarvis_play_media` is exposed to Assist to handle music requests.
   - `home-assistant/automations/jarvis_voice_music_playback.yaml` catches
     play commands before the conversation agent and routes by trigger id: artist phrasings
     ("play some X", "put on X") resolve the artist via search, then start
     an endless artist mix (`music_assistant.play_media`, `radio_mode: true`)
     so playback continues past the first track; song, album, and playlist
     phrasings keep narrower lookups. Individual song requests play immediately
     and continue autoplaying tracks by that artist (`music_assistant.play_media` with `enqueue: replace_next`).
   - Stopping is deterministic and local: `home-assistant/automations/jarvis_music_stop.yaml`
     pauses the satellite speaker on "turn off [the] music" / "turn [the] music
     off" (built-in intents already cover "stop the music"). Never route music
     stop through the conversation agent: on 2026-09-18 "turn off the music" fell to the
     local LLM and
     died on a poisoned chat log while "stop the music" paused in 13 ms.
   - Skipping is deterministic and local: `home-assistant/automations/jarvis_music_skip.yaml`
     advances the satellite speaker on "skip", "skip [this] song", and "next song/track".
     Never route music skip through the conversation agent.
   - Volume control is deterministic and local: `home-assistant/automations/jarvis_music_volume.yaml`
     handles semantic volume nudges ("turn up/down the volume a bit" -> ±10%,
     "turn it up/down a lot" / "crank it up" -> ±40%, default nudge -> ±10%, mute, unmute).
   - Platform parameter supports `spotify` (default) and `youtube_music` (`ytmusic`).
   - Music Assistant runs on `homelab-05` host network
     (`gitops/music-assistant/server.yaml`) and routes audio to the satellite speaker.
   - YouTube Music streaming requires a Proof-of-Origin (PO) token server; the
     `pot-provider` companion container (`brainicism/bgutil-ytdlp-pot-provider:1.2.1`)
     runs on `homelab-05` at `http://127.0.0.1:4416`.

## Conversation prompt (canonical)

The canonical prompt source is
`home-assistant/conversation/jarvis_prompt.md`. Paste its fenced text
verbatim into the Jarvis cloud fallback agent config entry. Keep that file and the live
copy in sync on any edit; the live copy is UI-managed config-entry state
with no read API, so behavioral regressions are caught by the live eval
lane (`just jarvis-eval-live`) rather than a state diff.

## Jev semantic command layer

The `jarvis_jev` custom conversation agent is the constrained fallback between
Home Assistant's local intents and the (currently unset) cloud conversation
agent. The preferred Assist pipeline must
keep `Prefer handling commands locally` enabled and select `Jarvis Jev Router`
as its conversation agent. The router delegates only high-confidence general
conversation to the fallback agent once one is configured; until then it
replies `General conversation is unavailable.`

The router sends the unmatched transcript to TypeSafe's hosted API using the
pinned `jev-1.13.0` model. This is a cloud disclosure and is not zero-retention
by default. It sends the full target allowlist as `criteria` (real entity IDs
including `climate.living_room_ac_living_room_ac_thermostat`,
`media_player.homelab_05_satellite_media_player_2`,
`scene.movie_low_living_room`); no entity registry, HA state dump, prompt,
or chat history.
The API key comes from GitLab CI/CD variable `TYPESAFE_API_KEY` through the
`home-assistant-secrets` ExternalSecret and container environment. Never put the
key in the config entry or Git.

Jev selects semantic IDs only. `router.py` owns the closed target and action
allowlists, compatibility checks, exact-number parsing, and confidence gates.
It maps accepted commands to fixed HA entity IDs and services. Jev never emits
an entity ID or service name. The initial automatic thresholds are 0.95 for
reversible controls and 0.98 for climate. The weakest required field wins.
These are conservative starting values and must be calibrated from recorded
distributions before lowering them.

The production decision stack runs the full-string deterministic L0 grammar
before calling hosted Jev. L1 output is advisory until the router independently
finds the selected target and action in the utterance. Color actions also
require the exact supported color, and compound syntax is rejected before any
service call. The release gate replays this same L0 then L1 path, excludes L0
cases from hosted latency, and calculates calibration only for commands the
stack would execute. It does not lower confidence thresholds to improve recall. Only the pinned hosted production model gates release (`--gate-model`, default `jev-1-13-0`); local and research fixtures are scored as advisory and cannot fail the run.

Fail-closed behavior is deliberate:

- medium or low-confidence home commands ask for a complete restatement;
- unknown targets, pronouns without an explicit target, incompatible
  target/action pairs, and malformed numeric arguments perform no action;
- compound requests perform no partial action and ask for one action at a time;
- TypeSafe timeout, authentication failure, overload, or malformed output does
  not restore any local-LLM HA control path (there is none since Phase 0);
- only a high-confidence `general_or_conversation` classification delegates to
  the fallback agent with the original conversation context, and replies
  `General conversation is unavailable` while no fallback is configured.

### Local decision shadow

Every request accepted by the Jev router is also submitted asynchronously to
`local-decision.voice.svc.cluster.local` when `JARVIS_SHADOW_ENABLED=1`
and the shadow deployment is scaled up (currently `replicas: 0` in prod). This shadow path cannot select the HA
route, execute a service, change speech, or delay the hosted Jev response. Only
one local request may be active at a time; another request is skipped rather
than queued behind CPU inference.

The shadow service runs `anthonym21/qwen3-0.6b-rlcd-decision` on the
`homelab-04` CPU. The model is pinned to Hugging Face revision
`b327ec5efb5fdbf8bfafa3b369720ac5f6434b05`; its loader verifies the published
weight hashes. The `eve-rlcd` inference package is pinned to commit
`ce5ebf627058b65acfc41d49e0e334a722a14ba5`, Python dependencies are locked,
and the CPU-only PyTorch wheel leaves the NVIDIA GPU free (Phase 0 removed
Ollama/Qwen; the T1000 is now available for Nemotron and the local
decision-model bench).
The model and runtime cache live under `/persist/voice-models/local-decision`
on `homelab-04`. First startup needs outbound access to GitHub, PyPI, the
PyTorch CPU index, and Hugging Face; steady-state inference is local.

Prometheus scrapes service request, failure, and inference-latency counters at
`/metrics`. Home Assistant logs one `jarvis_shadow` record per completed
comparison with a random request ID, field agreement count, total fields,
overall agreement, and local latency. Logs and metrics never include the
transcript, target, action, or other selected values. The hosted Jev result
remains authoritative until a recorded HA-specific evaluation demonstrates
acceptable accuracy and calibrated thresholds. The current model supports at
most 26 choices per question and is a research checkpoint, not a production
Jev reproduction.

The initial CPU smoke test used about 2.15 GiB RSS, loaded in 2.10 seconds, and
answered a two-choice light-action request in 0.30 seconds on the development
machine. The T600 is intentionally unused: this checkpoint is fp32 and its
published loader does not provide a validated 4-bit path that fits alongside
the existing GPU workload.

Deployment requires three operator steps after the Git change is reviewed and
published: create the protected/masked `TYPESAFE_API_KEY` GitLab variable,
reconcile Home Assistant through Flux, then add the `Jarvis Jev Router`
integration in HA with an empty fallback agent. Finally, select the router
in the preferred Assist pipeline and re-run
`just jarvis-eval-live`. The local corpus must remain local; Jev-specific
paraphrases must execute one expected action; ambiguous and failure cases must
execute zero actions.

## Conversation lifetime

The Wyoming satellite entity reuses one conversation across wake-word
activations until the chat session expires (verified against the running
HA 2026.9.1 `assist_satellite` code: each run passes the previous
conversation id, so separate "Hey Jarvis" invocations share context).
There is no per-wake reset setting. The `Max history messages: 4` cap applies
to the future cloud agent once configured, and the
canonical prompt's pronoun-safety rules force a clarification question when
"it" / "them" has no unambiguous antecedent. Note the `Prompt:` line in HA
debug logs shows the pre-trim history, not what the model received.
Prefer local intent handling for commands that must not depend on context
at all. There is no local model residency to maintain since Phase 0
(Qwen/Ollama removed, KV warmup automation deleted).
Conversation state and decision-model residency are separate.

## Self-talk loop defenses

Two layers against the 11:12-11:31 feedback loop (soundbar TTS re-entering
the QuadCast, Whisper hallucinating fragments like `Stu.` / `Control.` /
`Govee, Govee, ...`, every turn continuing the conversation and speaking
again). Layer 1 is live as of 2026-09-18; layer 2 is implemented, unit-tested,
and shipped via `configMapGenerator` from `gitops/voice/voice-id/`, pending
a push for Flux to roll it to the whisper pod:

1. Muting the satellite during TTS was tried via
   `home-assistant/automations/jarvis_satellite_tts_mic_mute.yaml` (mute on
   `responding`, unmute 300 ms after `idle`) and FAILED on 2026-09-18: LVA's
   mute switch is a user shut-up control, not a capture gate. Engaging it
   runs `tts_player.stop()` (killing the in-flight reply ~100-250 ms in) and
   plays mute/unmute chimes through the same single mpv instance, so every
   reply was truncated and chimed over. It also mistimes: HA reports `idle`
   at pipeline end while the mp3 keeps playing. The file stays in Git
   disabled (`initial_state: false`) as a record; do not re-enable without
   a satellite build whose mute path skips stop and chimes. Barge-in during
   TTS remains unavailable by design (single mpv, no duplex path).
2. Transcript sanity guard (`is_garbage_transcript` in
   `gitops/voice/voice-id/proxy.py`, unit-tested in
   `tests/test_transcript_guard.py`, shipped via `configMapGenerator` as
   `wyoming-voice-id-config-<hash>`, so every proxy or profile change
   rolls the whisper pod): drops empty transcripts, a single
   token repeated 3+ times, and a tight denylist of observed hallucinations.
   Dropped transcripts are forwarded as empty text, which HA abandons
   silently. Never denylist bare `Hey Jarvis.` (keeps the lone-wake-word
   acknowledgement) or `Stop.` (the loop escape hatch).

Deliberately not done: semantic similarity against the last TTS text needs
a TTS-text feed into the proxy that does not exist yet; the mute gate
already covers the window where semantic echo occurs. Revisit if echo
turns survive both layers.

## TTS (Chatterbox Turbo primary, Piper fallback)

`gitops/voice/chatterbox.yaml` runs a custom Wyoming TTS bridge
(`gitops/voice/chatterbox-bridge/server.py`, offline-tested in
`tests/test_chatterbox_bridge.py`) serving Chatterbox Turbo 350M
(`chatterbox-tts==0.1.7`, weights `ResembleAI/chatterbox-turbo` pinned at
`749d1c1a`) on the `homelab-05` T600 as `wyoming-chatterbox:10201`.
Chatterbox has no upstream Wyoming image, hence the in-repo server; it
answers `describe`/`synthesize` (plus buffered streaming synthesize) with
24 kHz 16-bit mono PCM. The stable TTS gateway selects Chatterbox first and
Piper second. HA remains bound to the gateway during backend failure.

GPU sharing: the bridge claims one `nvidia.com/gpu` resource on the T600 and
uses `NVIDIA_VISIBLE_DEVICES=all`. Immich ML was moved to CPU before this
allocation, leaving Turbo as the only model resident on that GPU. Turbo needs
roughly 2 GB at steady state.

Voice enrollment: drop one clean 5+ second English reference clip at
`/persist/voice-models/chatterbox/voices/jarvis.wav` on `homelab-05`
(the directory is created empty by the manifest). Conditionals are
prepared once at startup; a missing or rejected clip falls back to the
model's builtin voice under the same `jarvis` name. `[laugh]`-style
paralinguistic tags are stripped by default (`CHATTERBOX_ALLOW_TAGS=1`
to keep them).

The bridge permits unwatermarked audio when Perth watermark support is
unavailable, and exports `jarvis_tts_unwatermarked_audio` as `1` in that
case. Set `CHATTERBOX_REQUIRE_WATERMARK=1` to fail startup instead. Synthesis
exceptions emit a Wyoming `error` event before any audio; the TTS gateway
buffers only the small text request and retries Piper once. If Chatterbox has
already emitted `audio-start`, the gateway closes that turn and never splices
Piper audio into the partial response.

Hugging Face, Torch, Triton, XDG, and Numba caches use writable subdirectories
under `/runtime/cache`; the model volume remains read-only to the bridge.

Bench results 2026-09-21 (live, `wyoming-chatterbox:10201`):

- GPU co-residency FAILED: the T1000 exposes 3.62 GiB usable with
  Nemotron resident at ~2.67 GiB, and Turbo load OOMs (`CUDA out of
  memory`, pod log). Sequential pipeline use does not help: both
  models stay resident in their own pods.
- CPU fallback (`CHATTERBOX_DEVICE=cpu`, 4 threads) serves correctly
  but is too slow for interactive use: `Done.` TTFA 1.92 s (RTF
  2.67x), a 2 s reply TTFA 3.05 s (RTF 1.52x).
- T600 verdict 2026-09-21: the bridge moved to `homelab-05` with a
  clean `nvidia.com/gpu` allocation after Immich ML was demoted to
  CPU (its indexing jobs run slower; photo serving unaffected).
  Turbo loads with ~2 GB headroom to spare, no OOM. Warmed latency:
  `Done.` TTFA 0.91 s, a 2 s reply 1.26 s. Better than CPU but still
  above Piper's sub-second steady state, so switching the Assist
  pipeline to the `jarvis` voice trades roughly half a second of
  responsiveness for voice quality plus cloning. That switch is a HA
  UI step (select the `wyoming-chatterbox` TTS in the preferred
  pipeline); no manifest change needed. To enroll the cloned voice,
  drop the 5 s+ clip at
  `/persist/voice-models/chatterbox/voices/jarvis.wav` on
  `homelab-05` and roll the pod. The orphaned
  `/persist/voice-models/chatterbox` tree on `homelab-04` can be
  reclaimed by hand.

## Satellite health

`gitops/voice/satellite.yaml` gates readiness on a `pgrep -f linux_voice_assistant`
exec check. Do not probe TCP port 6053: `linux-voice-assistant` implements the
single-client ESPHome native protocol where any unauthenticated raw TCP
probe immediately overwrites `state.satellite` and, upon closing, clears it to
`None` (`ha_connected: false`), breaking the HA connection and disabling all
audio processing. Liveness and startup keep the same `pgrep` exec checks.
The HA-level `jarvis_satellite_watchdog.yaml` automation stays as the
higher-level check for HA losing the satellite.

## Pinned images

Voice images are pinned by digest in their manifests (repo convention, with the
source tag kept in a trailing comment). The current pins and their upstream
sources for refresh:

| Deployment | Image | Pin | Refresh |
| --- | --- | --- | --- |
| satellite | `ghcr.io/ohf-voice/linux-voice-assistant` | `1.1.1` | tags list on GHCR; `latest` floats past releases, so pin the newest stable tag, not `latest` |
| whisper | `docker.io/rhasspy/wyoming-whisper` | `3.8.1` | Docker Hub tags, newest `3.x` |
| piper | `docker.io/rhasspy/wyoming-piper` | `2.5.2` | Docker Hub tags, newest non-`omnivoice` `2.x` |
| chatterbox bridge base | `registry.rupan.dev/upstream/ghcr.io/astral-sh/uv` | same digest as von/local-decision | GHCR tags, newest stable; `chatterbox-tts==0.1.7` and Turbo weights `749d1c1a` pinned in `gitops/voice/chatterbox.yaml` |
| pot-provider | `docker.io/brainicism/bgutil-ytdlp-pot-provider` | `1.2.1` | Docker Hub tags, newest stable |

Ollama (`docker.io/ollama/ollama`, was `0.34.1` with `qwen2.5:3b`) was
removed in Phase 0 (`gitops/voice/ollama.yaml` deleted) to free the T1000
GPU. Do not re-add a local LLM row without revisiting the cloud-LLM
decision.

Refresh with `just check-changed` and `just check` before handoff. Bumping the
satellite past `1.1.1` re-derives entity behavior; re-verify the canonical IDs
above after any satellite bump.

## Model contract (retired in Phase 0)

There is no locally served LLM. `gitops/voice/ollama.yaml` (Deployment,
Service, and the `ollama-qwen2-5-3b-ready` pull plus warmup Job for
`qwen2.5:3b`, 1.9 GB) was deleted to free the 4 GB T1000. The HA Ollama
config entry (`home-assistant/integrations/ollama_01M2PAGP.yaml`) was
removed from Git and must also be deleted in the HA UI, and the
`jarvis_prompt_warmup` KV-cache warmup automation was deleted with it.
General conversation stays unavailable until a cloud fallback agent is
configured on the `Jarvis Jev Router` entry.

## Model storage

All remaining model caches live on `homelab-04` local NVMe at
`/persist/voice-models/{whisper,piper,local-decision,chatterbox}`
via `hostPath` (chatterbox holds `models/`, the pip/CUDA `runtime/`,
and the operator-supplied `voices/jarvis.wav` reference clip). This is
deliberate: every consumer is already pinned to `homelab-04`, local disk
removes the NAS as a voice dependency and loads multi-GB models off NVMe
instead of 1 GbE NFS. The caches are fully re-derivable (re-pull on empty
dir), so they are not backed up. The orphaned `/persist/voice-models/ollama`
directory on `homelab-04` and the orphaned NFS directories from the
retired PVCs can be reclaimed by hand.

## Latency notes

- There is no local LLM since Phase 0, so no VRAM residency to maintain
  and no cold KV-cache evaluation. Per-turn latency is STT plus Jev API
  plus TTS. Keep Assist exposures pruned anyway: a lean tool schema keeps
  the future cloud prompt cheap.
- Whisper runs `base.en` (~140 MB) on `homelab-04` CPU with `--beam-size 5`
  (HA upstream default on non-ARM) and fixed `--language en`. `base.en`
  completes transcription in ~300ms on CPU at beam 1; beam 5 keeps multiple
  hypotheses alive through short ambiguous utterances at modest extra CPU
  cost, still far below GPU-less `turbo`. `turbo` (large-v3-turbo, ~800 MB)
  was benchmarked and rejected: without
  GPU acceleration, `turbo` on CPU incurs a ~6.0s transcription delay per turn,
  causing the satellite to appear to stall or listen long after the user stops
  speaking. `base.en` restores sub-second turn responsiveness.
- Piper is sub-second at steady state but re-downloads its voice on every pod
  restart unless its model cache persists. Voices persist on the
  `piper-voices` PVC (`gitops/voice/storage.yaml`); do not revert that volume
  to `emptyDir`.
- Model pulls (Whisper, Piper) hit the network only on first-ever
  start; after that they load off local NVMe.
- The face is a Cozmo-style procedural eye engine in
  `home-assistant/www/jarvis/app.js`: expression presets per state (eye
  scale plus upper/lower lids with y/angle/bend, with a right-eye
  asymmetry channel for the confused thinking squint) layered with a
  look assistant (saccades) and a blink assistant (vertical squash),
  all interpolated every animation frame and drawn flat (no glow) on
  `#face-canvas`, with CRT scanlines plus a cached vignette for the
  old-display feel. State colors come from the `#app.state-*` CSS
  variables (`--eye-bg`, `--primary-glow`), overridable per state via
  `face_color_<state>` in `home-assistant/www/jarvis/config.json`.
  Keys 1-6 / click cycle states for visual testing without the pipeline.
  The `music` state replaces the face with a full-screen procedural
  visualizer (56-bar spectrum plus a mirrored waveform ribbon, standard
  blue via `--eye-bg`, flat fills only; no audio tap on the kiosk, so it is
  layered sines rather than real FFT) and shows `Title - Artist` from the
  media player attributes in the status label (`JARVIS // PLAYING //
  <track>`). It shows when the satellite media player is `playing` while
  the satellite itself is idle; mute and the voice states (`listening`,
  `processing`, `responding`) keep priority over it, so music never
  masquerades as speaking.
- The face does no canvas shadows or blur, so face rendering should not
  jank the T600; if it does, suspect the pipeline first.
- Face deploys are self-updating. `home-assistant/www/jarvis/` lives on the
  HA PVC (`/config/www/jarvis/`), not in the ConfigMap, so `ha-apply` does
  not cover it: sync with `kubectl cp` into the home-assistant pod, then
  bump `face_version` in `config.json` and the kiosk `?v=` in
  `flake/hosts/homelab-05/default.nix` to match. The face polls
  `config.json` every 60s and self-navigates to the new version, bypassing
  the kiosk browser cache. No kiosk restart is needed.

## Observability

### Generated topology and drift checks

The non-secret topology report is generated from the Flux manifests and, when
requested, read-only Kubernetes and Home Assistant inspection:

```sh
just voice-topology
just voice-topology-check
```

The report records every voice Deployment and desired replica count, the
ordered STT and TTS gateway backends, Service selectors and targets, live
endpoints, and the Assist pipeline engines. A failed live read is reported as
`unknown`; it is not evidence that a resource was deleted or unhealthy. The
drift check validates the Git-owned invariants only. Deployment and live
reconciliation remain Flux and operator actions.

The stable HA targets are `wyoming-whisper.voice:10300` for STT and
`wyoming-chatterbox.voice:10201` for TTS. The gateways choose their backend
preference internally, so replacing Nemotron with Whisper or Chatterbox with
Piper does not change the HA pipeline identity.

Recovery follows the same boundary:

- If a gateway pod is unavailable, restore its Deployment and Service through
  Flux. HA should remain pointed at the stable Service names.
- If the primary backend is unavailable, the gateway opens its circuit and
  uses the configured fallback. Check the gateway backend availability and
  fallback counters before changing HA.
- If all backends are unavailable, the gateway stays unavailable and HA must
  surface the failed pipeline. Do not point HA directly at a backend as a
  manual workaround.
- If the generated report shows HA pipeline drift, inspect the UI-managed
  config-entry state and correct the pipeline in HA. Do not silently edit or
  overwrite UI-managed state from Git.

`gitops/voice/exporter.yaml` runs `jarvis-exporter`, a dependency-free
Python exporter that holds the HA websocket, tracks satellite state
transitions per turn, and counts `call_service` bus events seen while the
satellite is processing. Scraped via the `jarvis-exporter` ServiceMonitor
into the kube-prometheus-stack Prometheus; visualized by the Jarvis Voice
Grafana dashboard (`grafana-dashboard-jarvis` ConfigMap).
The exporter reaches Home Assistant through a Cilium policy for the
`kube-apiserver` entity, restricted to TCP port 8123: HA uses host networking,
so ordinary NetworkPolicy IP blocks do not match its node identity.

Metrics and their exact semantics:

- `jarvis_requests_total{satellite}`: turns that reached idle through responding.
- `jarvis_stt_latency_seconds{satellite}`: listening-phase duration, which is
  utterance time plus transcription. It is not pure STT compute; compare
  turns, not against compute budgets.
- `jarvis_llm_latency_seconds{satellite}`: processing-phase duration.
- `jarvis_tts_latency_seconds{satellite}`: responding-phase duration.
- `jarvis_tool_calls_total{satellite}`: service calls on the shared bus
  during processing. Adjacent automations firing mid-turn can inflate this.
- `jarvis_failed_requests_total{satellite,stage}`: turns abandoned from
  processing or responding without ever responding. Listening→idle cancels
  are tracked separately in `jarvis_aborted_requests_total`.
- `jarvis_requests_by_room_total{room}`: completed turns attributed to the
  area of the first targeted entity, else `unknown`.
- `jarvis_turn_speed_class_total{satellite,class}`: completed turns split
  by processing-phase speed, where `class` is `fast` or `slow`. This is a
  latency metric, not a routing label: turns whose processing phase fits
  within `FAST_TURN_MAX_SECONDS` (default `2.0`) count as fast, slower
  turns as slow. Routing ground truth is the `processed_locally` field on
  Assist intent traces, never this series.
- `jarvis_exporter_ha_connected`: 1 while the websocket is authenticated.
- `jarvis_exporter_subscription_state{state}`: active websocket state, with
  `subscribed` required for readiness.
- `jarvis_exporter_reconnects_total`: reconnect attempts (first connect is not
  counted), preserved across websocket reconnects.
- `jarvis_exporter_auth_failures_total`: rejected HA authentication attempts.
- `jarvis_exporter_last_event_age_seconds`: age of the last received websocket
  frame, used to detect a stale subscription.

The exporter liveness probe only proves its process and HTTP loop are alive.
Readiness requires authenticated HA subscriptions. Prometheus should alert
when `jarvis_exporter_ha_connected` is zero, the subscription state is not
`subscribed`, or the last event age is stale.

The exporter needs a Home Assistant long-lived token in the GitLab project
variable `JARVIS_EXPORTER_HA_TOKEN` (consumed via the `gitlab-project`
ClusterSecretStore). Without it the exporter still serves `/metrics` with
`connected=0`, and the dashboard shows no turns. Exporter logic is covered
by `tests/test_jarvis_exporter.py`, which executes the exact script embedded
in the ConfigMap.

### Eval loop

`tests/jarvis_voice_eval_corpus.yaml` holds 30 to 60 canonical commands with
their expected routing (`local` vs `llm`), target entities, tools, and replies.
`tests/test_jarvis_voice_eval.py` keeps the corpus consistent offline: local
entries must name an intent overridden in
`home-assistant/custom_sentences/en/jarvis_terse.yaml`, reply `Done.`, and
target only entities defined in `home-assistant/core/configuration.yaml` or
`home-assistant/scripts/`.

Whenever the model, prompt, Whisper version, or exposures change, run the
corpus live with `just jarvis-eval-live` (`python -m scripts.jarvis_eval`,
`--select <id>` for a subset, `--json` for machine output). The runner
drives `assist_pipeline/run` from the `intent` stage to the `intent` stage
with text input (real pipeline routing, bypassing only wake word and STT;
local-path device actions still execute, so run it when someone is home).
Local cases must route locally with the expected reply and no action outside
`targets`; llm cases must fall through (`processed_locally: false`) and,
until a cloud fallback lands, reply `General conversation is unavailable`
(asserted by the live eval runner).
Full cloud-LLM behavior (tool choice, phrasing) stays manual once configured. Keep a smaller acoustic
suite (10 to 20 representative commands) for microphone to Whisper
regressions; intent routing and STT are separate concerns. The manifest file
(`tests/acoustic/manifest.json`) must be committed (labels only; recordings
stay gitignored per `.gitignore`).

## Voice-ID speaker recognition

### Architecture and proxy intercept

Home Assistant Assist consumes Wyoming STT events (`audio-start`, `audio-chunk`,
`audio-stop`), but discards raw audio once transcribed. To achieve speaker-aware
routing and personalization without modifying Home Assistant core:

1. A lightweight Wyoming proxy (`gitops/voice/voice-id/proxy.py`,
   importable in tests as `scripts.voice_id.proxy` via symlink) runs as a
   sidecar in `wyoming-whisper` pod (`gitops/voice/whisper.yaml`) listening on port `10300`.
   The actual Whisper STT engine listens upstream on `127.0.0.1:10301`.
2. As the client streams audio, the proxy transparently forwards audio events to
   Whisper while buffering the 16kHz PCM audio in memory.
3. Upon `audio-stop`, the proxy uses `sherpa-onnx` CAMP++ ONNX model
   (`3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx`) to compute a 512-dimensional
   speaker embedding vector in ~35ms on CPU.
4. Audio samples shorter than 3.5 seconds are tiled to 3.5s to provide the
   receptive field required by the neural network for high-confidence classification.
5. The embedding is scored via cosine similarity against enrolled profiles
   in `profiles.json` (shipped as SOPS-encrypted Secret `voice-id-profiles`
   in the `voice` namespace, mounted at `/etc/voice-id/profiles.json`; the
   plaintext file stays out of git).
6. If the top score exceeds `threshold` (0.35) and exceeds the runner-up by
   `min_margin` (0.10), the speaker identity is confirmed (`Rupan` or `Sam`).
7. When Whisper returns the `transcript` event, the proxy prefixes `speaker <Name>`
   (e.g. `speaker Sam play some Kanye`) before sending it back to Home Assistant.
8. `home-assistant/automations/jarvis_voice_music_playback.yaml` and
   `home-assistant/scripts/jarvis_play_media.yaml` read the speaker identity and route
   music requests directly to Sam's YouTube Music or Rupan's Spotify.

### Speaker enrollment procedure

To add or update speaker voice profiles:
1. Obtain ~20-30s of clear speech in any format (.wav, .m4a, .mp3). Prefer
   far-field samples recorded through the satellite mic itself
   (`pw-record --target=<quadcast-id> --rate=16000 --channels=1`) from the
   usual speaking spot: close-mic enrollment scores poorly against living-room
   audio. Re-enrolling one speaker must keep the others: pass the existing
   enrollment wavs (kept next to the model) for unchanged speakers, since the
   script rewrites the whole file. Keep threshold 0.35 / min_margin 0.10 as
   written by `scripts/voice_id/enroll.py` (a copy under the model volume has
   different defaults; the repo script is authoritative).
2. Convert to 16kHz mono WAV:
   ```sh
   ffmpeg -i user.m4a -ar 16000 -ac 1 /tmp/user.wav
   ```
3. Run the enrollment script inside the voice container or development shell:
   ```sh
   python3 scripts/voice_id/enroll.py \
     --model /persist/voice-models/whisper/voice-id/model.onnx \
     --speaker Rupan /tmp/rupan.wav \
     --speaker Sam /tmp/sam.wav \
     --output /tmp/profiles.json
   ```
4. Fold the result into the tracked SOPS secret (never edit the
   ciphertext by hand; re-encrypt through `sops` so the MAC stays valid):
   ```sh
   export SOPS_AGE_KEY_FILE=$PWD/secrets/keys/flux-age.key
   sops -d gitops/secrets/voice-id-profiles.sops.yaml > /tmp/voice-id.dec.yaml
   python3 - - <<'EOF'
   import sys, base64, yaml
   raw = open('/tmp/profiles.json','rb').read()
   d = yaml.safe_load(open('/tmp/voice-id.dec.yaml'))
   d['data']['profiles.json'] = base64.b64encode(raw).decode()
   yaml.safe_dump(d, open('/tmp/voice-id.dec.yaml','w'))
   EOF
   sops -e --age age1k2vhn663mmw9ancuwm2hfmtfg4xyus9xlvp8rkep4fpvxlqa9gdsms9usl \
     /tmp/voice-id.dec.yaml > gitops/secrets/voice-id-profiles.sops.yaml
   shred -u /tmp/voice-id.dec.yaml /tmp/profiles.json
   ```
   Commit; Flux decrypts via the `secrets` Kustomization and the whisper /
   nemotron pods mount the new Secret on roll. Never commit plaintext
   `profiles.json`.


## Failure symptoms

- Face stuck on `CONNECT SATELLITE` prompt: kiosk token missing from
  the browser profile. Every reboot wipes it by design (tmpfs root,
  `flake/modules/common-base.nix`): `/home/kiosk` is recreated empty,
  so a fresh Chromium profile has no token until
  `kiosk-ha-token-seed.service` runs. Check
  `systemctl status kiosk-ha-token-seed` on `homelab-05` and confirm
  `/persist/secrets/jarvis-kiosk-ha-token` exists (root, mode 600).
  Manual fallback is entering the token once in the kiosk browser.
- Face shows IDLE but never LISTENING: satellite pod not ready or HA Wyoming
  entry pointing at the wrong host. `just status cluster` plus the satellite
  pod events; confirm the HA Wyoming host is `voice-satellite.voice`.
- First turn after deploy is very slow, later turns fine: Whisper or Piper
  cold start. Check probe status on whisper/piper before tuning anything.
- Every turn slow: Whisper CPU throttled. Check pod resource usage.
- Face state frozen while voice works: HA websocket broken in
`app.js`; use keys 1-6 on the kiosk keyboard to confirm the face itself
still cycles states.
- Mic not detected or satellite in CrashLoopBackOff: verify Pipewire/WirePlumber
  sees the QuadCast on `homelab-05` via `wpctl status`. WirePlumber assigns it
  top priority 3500 (`~alsa_input.*QuadCast.*`), while sink monitor sources are
  lowered to 100. `satellite-alsa-restore.service` unmutes the QuadCast `Mic`
  ALSA control when that card is present. The satellite container
  matches on substring `QuadCast` (first substring hit wins, so if several
  QuadCast nodes appear, confirm the USB source is listed first). For the exact
  Pulse source names the container sees, run it once with `LIST_DEVICES=1`.
- Soundbar loses sound after switching inputs: HDMI audio requires active video
  clocking. On `homelab-05`, `satellite-hdmi-audio-clock.service` runs as a
  continuous daemon re-clocking `HDMI-A-2` via `wlr-randr` whenever the soundbar
  reconnects. The soundbar PCM is onboard `pro-output-3`, pinned as default
  output at WirePlumber priority 3000 (measured 2026-09-18: the only onboard
  PCM reaching the soundbar); Nvidia HDMI stays at 2000, onboard fallback at
  1000, QuadCast headphone jack at 500. If the soundbar slept through silence,
  its kernel audio descriptor goes stale (`eld_valid 0` under
  `/proc/asound/PCH/eld*` while EDID is still present): toggle `HDMI-A-2` off
  and on via `wlr-randr` as the kiosk user to force a modeset and repopulate
  the ELD, then confirm with a test tone. Check
  `systemctl status satellite-hdmi-audio-clock` if soundbar audio does not return.
  The daemon keeps the ViewSonic on HDMI-A-1 at 1920x1080, position 0,0, and
  the soundbar output on HDMI-A-2 at its supported 1024x768 mode, position
  0,0 (overlapping to keep canvas width at 1920x1080 and the kiosk face centered).
  It reads both outputs and reapplies the combined layout only when
  enabled state, mode, or position drifts, avoiding repeated compositor
  reconfiguration while preserving the video clock HDMI audio needs.
- PipeWire WebRTC echo cancellation evaluated 2026-09-18 and parked: the
  `libpipewire-module-echo-cancel` source exposed the mic with 0.0 dB measured
  reduction on both tonal and wideband playback (correct links, correct
  reference, 10 s stationary signal). Suspected USB-mic vs HDMI clock drift
  defeating the canceller. Do not retry without a new measurement showing
  cancellation. The deterministic mitigations are mic mute during TTS and a
  transcript sanity guard in the voice-id proxy (both pending as of 2026-09-18).
