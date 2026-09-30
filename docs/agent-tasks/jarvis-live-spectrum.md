# Agent Task: jarvis-live-spectrum

Status: `complete`

Base commit: `0d89a8399e5de30cadbab2d65ebfa155b27714d4`

Checkpoint HEAD: `0d89a8399e5de30cadbab2d65ebfa155b27714d4`

Owner: `muse`

Session: `heather-deimos`

Exported at: `2026-09-30T18:40:23.924966+00:00`

Current HEAD at export: `fd32f669ded731191f643764814f97f6a2fa185f`

## Objective

Drive the Jarvis music face cava bars from the real soundbar audio spectrum

## Acceptance criteria

- [x] Face renders live FFT bars from the spectrum daemon while music plays; procedural sines remain as offline fallback (Committed in c08de62; flake/modules/jarvis-spectrum.py daemon runs cava FFT over PipeWire and exposes SSE on localhost)
- [x] Spectrum daemon serves Soundbar-monitor cava frames over localhost SSE with /healthz (Enabled on homelab-05 and wired to kiosk face v19)
- [x] Offline gates pass (face tests, spectrum tests, nix eval) (tests/test_jarvis_spectrum.py passes 208 lines of unit tests offline)

## Owned source

- `flake/modules/jarvis-spectrum.nix`
- `flake/modules/jarvis-spectrum.py`
- `flake/hosts/homelab-05/default.nix`
- `home-assistant/www/jarvis/`
- `tests/test_jarvis_spectrum.py`
- `tests/test_jarvis_face.py`
- `tests/test_jarvis_face_harness.js`
- `docs/runbooks/jarvis-voice.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `20713d2adbb519587f2d861100a4c3beca236524c8b0560d39b4843cfb6a0013`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- None recorded

## Next action

None. Deployed and verified.

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
