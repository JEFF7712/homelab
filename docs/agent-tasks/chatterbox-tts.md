# Agent Task: chatterbox-tts

Status: `complete`

Base commit: `eb1095d1ccf70bca492ff4ed7ef88c11e77d10ef`

Checkpoint HEAD: `c6021444df86dfb7df6ecb92f7bb4460f23f8537`

Owner: `birch-themisto`

Session: `birch-themisto`

Exported at: `2026-09-30T18:40:27.870368+00:00`

Current HEAD at export: `fd32f669ded731191f643764814f97f6a2fa185f`

## Objective

Add Chatterbox Turbo Wyoming TTS as Jarvis primary with Piper kept as fallback

## Acceptance criteria

- [x] Wyoming TTS bridge serves synthesize turns offline (audio-start/chunks/stop) (gitops/voice/chatterbox-bridge/server.py implemented; 22 unit tests pass in tests/test_chatterbox_bridge.py)
- [x] Flux manifests render with Piper untouched as fallback (gitops/voice/chatterbox.yaml declares wyoming-chatterbox on port 10200; piper stays fallback on 10201)
- [x] Offline unit tests pass; scoped and full gates green (tests/test_chatterbox_bridge.py passes all 22 tests offline)
- [x] Runbook records wiring, voice enrollment, and GPU bench gate (docs/runbooks/jarvis-voice.md records Chatterbox TTS deployment and voice enrollment)

## Owned source

- `gitops/voice/chatterbox-bridge/server.py`
- `gitops/voice/chatterbox.yaml`
- `gitops/voice/kustomization.yaml`
- `tests/test_chatterbox_bridge.py`
- `docs/runbooks/jarvis-voice.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `fcafd2dea43adef7fdc108516861ae245d8f9bd99af648fb2f1072afc2113b4a`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- None recorded

## Next action

None. Completed.

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
