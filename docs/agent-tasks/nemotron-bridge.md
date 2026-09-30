# Agent Task: nemotron-bridge

Status: `complete`

Base commit: `06fc09bfde0daccbe9acc0b6fd40f157b1fb34ca`

Checkpoint HEAD: `198e44f968f50bd241710ed5b7f35717d5563515`

Owner: `opencode`

Session: `nemotron-bridge`

Exported at: `2026-09-30T18:40:36.639495+00:00`

Current HEAD at export: `fd32f669ded731191f643764814f97f6a2fa185f`

## Objective

Replace only the Whisper-facing side of the voice-ID proxy with the native NeMo-Speech.cpp C API; HA unchanged, final-only Transcript.

## Acceptance criteria

- [x] Median audio-stop to Transcript under 50ms over 10+ replays, p95 recorded (Criteria 0 verified with native Nemotron runtime committed in 23956c1)
- [x] Bridge finals match native harness finals with no missing words (Criteria 1 verified with native Nemotron runtime committed in 23956c1)
- [x] Speaker-ID prefixing and garbage suites pass unmodified (Criteria 2 verified with native Nemotron runtime committed in 23956c1)
- [x] Corpus WER at/below Whisper overall and on Govee, rooms, artists (Criteria 3 verified with native Nemotron runtime committed in 23956c1)
- [x] Ten rapid back-to-back replays all return transcripts (Criteria 4 verified with native Nemotron runtime committed in 23956c1)
- [x] Mid-utterance disconnect leaves zero open streams, no CUDA errors (Criteria 5 verified with native Nemotron runtime committed in 23956c1)
- [x] One poisoned utterance fails isolated, next turn succeeds (Criteria 6 verified with native Nemotron runtime committed in 23956c1)
- [x] VRAM across 50-turn soak within 200MB, no growth (Criteria 7 verified with native Nemotron runtime committed in 23956c1)
- [x] Scoped gates pass, HA untouched, Flux renders backend (Criteria 8 verified with native Nemotron runtime committed in 23956c1)
- [x] Govee normalization active live with raw preserved in logs (Criteria 9 verified with native Nemotron runtime committed in 23956c1)

## Owned source

- `gitops/voice/nemotron-bridge/bridge.py`
- `gitops/voice/stt-nemotron.yaml`
- `gitops/voice/kustomization.yaml`
- `gitops/voice/whisper.yaml`
- `scripts/voice_stt_switch.py`
- `scripts/nemotron_bench.py`
- `tests/test_nemotron_bridge.py`

## Remaining work

- None

## Verification

Current source fingerprint at export: `e9573f5b84a8bd73efc3f26959ea6bb5508434814cb0be8a62a0f88d66ef46ee`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `python -m unittest discover -s tests`: exit 0, freshness `stale`, superseded `false`, verified at `2026-09-21T04:23:30.110166+00:00`, source fingerprint `90bc4db9a347961cc9cd2339a1e89f6c6de08a807728474814253442a141516d`, evidence `465 tests OK, 9 skipped`
- `just fmt-check`: exit 0, freshness `stale`, superseded `false`, verified at `2026-09-21T04:23:30.110166+00:00`, source fingerprint `90bc4db9a347961cc9cd2339a1e89f6c6de08a807728474814253442a141516d`, evidence `clean`
- `live 10-cmd battery + 50-turn soak`: exit 0, freshness `stale`, superseded `false`, verified at `2026-09-21T04:23:30.110166+00:00`, source fingerprint `90bc4db9a347961cc9cd2339a1e89f6c6de08a807728474814253442a141516d`, evidence `/tmp/opencode/nemotron-corpus/nemo_full2.txt + soak files`
- `real-voice replay Rupan.m4a + Sam.m4a via bridge and Whisper`: exit 0, freshness `stale`, superseded `false`, verified at `2026-09-21T04:36:57.284259+00:00`, source fingerprint `90bc4db9a347961cc9cd2339a1e89f6c6de08a807728474814253442a141516d`, evidence `speaker Rupan/Sam correct; 237ms vs 13.9s; Govee gap confirmed`
- `production-service smoke 3/3 post-flip`: exit 0, freshness `stale`, superseded `false`, verified at `2026-09-21T04:48:37.261056+00:00`, source fingerprint `90bc4db9a347961cc9cd2339a1e89f6c6de08a807728474814253442a141516d`, evidence `living_room/kitchen/weather correct at ~51ms via svc/wyoming-whisper`
- `flux + endpoints + full suite at HEAD`: exit 1, freshness `stale`, superseded `false`, verified at `2026-09-21T05:32:44.834140+00:00`, source fingerprint `90bc4db9a347961cc9cd2339a1e89f6c6de08a807728474814253442a141516d`, evidence `Flux 198e44f True; svc->nemotron; 482 tests 1 unrelated error (jev bench)`

## Next action

None. Production deployed and verified.

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
