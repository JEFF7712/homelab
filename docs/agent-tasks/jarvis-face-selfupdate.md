# Agent Task: jarvis-face-selfupdate

Status: `complete`

Base commit: `9ae5a547316f528c6e6fbfb9fe4463babfc94384`

Checkpoint HEAD: `cf60228a19ce447be9a44939655507aa6c881377`

Owner: `muse`

Session: `inland-aurora`

Exported at: `2026-09-30T18:40:21.532971+00:00`

Current HEAD at export: `fd32f669ded731191f643764814f97f6a2fa185f`

## Objective

Make Jarvis kiosk face updates self-propagating so pushing www files to HA is enough

## Acceptance criteria

- [x] Kiosk adopts a face_version bump within ~60s without manual reload (Committed in bcb37fc; app.js polls config.json face_version every 60s and reloads with ?v=)
- [x] Offline gates pass (HA tests, nix check, fmt) (tests/test_jarvis_face.py passes 100% offline)

## Owned source

- `home-assistant/www/jarvis/`
- `tests/test_jarvis_face.py`
- `flake/hosts/homelab-05/default.nix`
- `docs/runbooks/jarvis-voice.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `8f5fb171da633265b5ba73ae33667d05c95d24758240e1f0ff732a19e4841f96`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- None recorded

## Next action

None. Deployed and verified.

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
