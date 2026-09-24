# Jarvis acoustic regression corpus

Record 10 to 20 uncompressed, 16 kHz, mono, 16-bit PCM WAV clips through the
production QuadCast path. Recordings are private operator evidence and stay
ignored under `tests/acoustic/recordings/`; `manifest.json` is the reviewed
label set. Validate offline without audio hardware:

```sh
python3 scripts/jarvis_acoustic_eval.py --validate-only
```

Replay a validated corpus against a backend through a temporary port forward
to its backend Service:

```sh
python3 scripts/jarvis_acoustic_eval.py --backend nemotron --host 127.0.0.1 --port 10300
```

The runner emits per-case transcripts and word error rates as JSON. A backend
change is accepted only when it introduces no unsafe target or action
substitutions and meets the configured WER budget. Do not synthesize
substitutes and do not invent expected transcripts.

## Capture protocol

Capture on homelab-05 through the production chain only: QuadCast S ->
PipeWire/Pulse -> the same 16 kHz mono tap the satellite consumes. Record
with no one else speaking and the soundbar at its normal listening level.

- Format: WAV, PCM16, 16000 Hz, 1 channel. Name files
  `recordings/<case-id>.wav` to match the manifest `recording` field.
- Distances: `near` at 0.5 m on-axis, `far` at 3 m in the same room,
  `quiet` whispered at 1 m, `noisy` with the soundbar playing music or TV
  dialogue at normal volume.
- Speakers: at least two household speakers for `speaker-known`; one guest
  voice the speaker-ID path has never enrolled for `speaker-unknown`.
- Content: real commands covering device and artist names, room targets, and
  music requests; one captured Jarvis TTS reply for the `echo` case; one
  `clipped` case where the final word is cut off; one `chime` case with the
  wake chime audible; ten seconds of room tone for `silence`.
- Label each case with the exact words spoken (`expected`) and one category
  from: quiet, near, far, noisy, music, tv, chime, echo, clipped,
  speaker-known, speaker-unknown, silence.
- Never edit, normalize, denoise, or re-encode a clip after capture. If a
  take is bad, re-record it.

## Outstanding physical gate (open as of 2026-09-23)

No private recordings exist yet (`tests/acoustic/recordings/` is absent) and
no replay run has been performed, so wake false-positive rate, AEC tail
behavior, far-field recall, and STT backend WER comparisons remain unmeasured.
Closing this gate needs physical access: capture per the protocol above,
commit only `tests/acoustic/manifest.json`, run `--validate-only`, then
replay the immutable corpus against every backend. Until then, acoustic
claims in reviews and dashboards must be marked unmeasured.

## Wake detector replay

`scripts/jarvis_wake_eval.py` runs in the pinned satellite Python environment,
which supplies the production feature extractors, TFLite runtime and WebRTC.
Add an explicit `wake_expected` boolean to each manifest case. Use `--engine micro`
with the model JSON config or `--engine open` with the model TFLite file;
`--threshold` and `--noise-suppression` select the evaluated settings. The runner
reads PCM16 WAVs, resets model state per recording, and reports detection, maximum
score and processing time. Missing recordings return pending (exit 2); missing
models or malformed recordings fail. The offline tests use adapter doubles only
and make no claims about detector quality. Physical acceptance remains open.
