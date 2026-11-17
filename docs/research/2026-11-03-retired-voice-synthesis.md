# Retired Jarvis speech synthesis

Historical stack and benchmark notes, archived during the November 3 docs audit.
The [current runbook](../runbooks/jarvis-voice.md) describes display-only replies.
These instructions require restoring the old integration and are not active
deployment procedures. Retained model data must be checked before recovery.

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
