---
name: jarvis-ha-debug
description: Debug Jarvis voice or a Home Assistant device behavior. Use when Jarvis says "done" but nothing happens, asks to "specify a device", misroutes music/lights, or when Govee/Tuya/Roku/Music Assistant gear misbehaves. Read-only until a fix is agreed.
---

# Jarvis HA Debug

Symptom-driven debugging for the Jarvis voice stack and HA devices. Canonical reference is `docs/runbooks/jarvis-voice.md`; this skill is the short path, not a copy.

## Signal chain (stable identities)

QuadCast mic on `homelab-05` -> satellite container (`voice-satellite.voice:6053`) -> Home Assistant -> stable STT gateway (`wyoming-whisper.voice:10300`) -> Jev Router conversation agent (cloud fallback: Google Generative AI) -> stable TTS gateway (`wyoming-chatterbox.voice:10201`) -> soundbar -> face state over HA websocket.

HA always points at the stable gateway services, never directly at a backend. If a backend fails, the gateway opens its circuit and falls back; check gateway backend availability and fallback counters before touching HA config.

## Symptom table (check in order)

- "Done" but nothing plays: the play automation missed its trigger, or Jev `play_music` low-confidence-clarified. Check `home-assistant/automations/jarvis_voice_music_playback.yaml` trigger coverage for the exact phrasing first, then Jev confidence. Music is the one action that never asks for device or room.
- "Please specify a device" on lights/music: Jev fail-closed behavior on pronouns without an explicit target, unknown targets, or incompatible target/action pairs. Bare on/off/color light commands should resolve locally to `light.downstairs_lights` via `jarvis_light_control.yaml` intents and never reach the agent; if they do, the local intent missed.
- Music stop/skip/pause failing: these are deterministic local automations (`jarvis_music_stop.yaml`, `jarvis_music_skip.yaml`, resume/volume/modes). Never route them through the conversation agent.
- Govee bulbs unreachable: distinguish LAN path from cloud path. A "Device Abnormal" notice after music mode points at cloud-side rate limiting, not local control.
- Wyoming Piper/STT connection failure in HA: check the gateway pod and its backend circuit before re-pointing any HA Wyoming entry.
- Face stuck on CONNECT SATELLITE: kiosk token missing from the browser profile; check `kiosk-ha-token-seed.service` on `homelab-05`. Face shows IDLE but never LISTENING: satellite pod or HA Wyoming host wrong (must be `voice-satellite.voice`).
- Soundbar silent: default sink must be the USB SPDIF adapter (`wpctl status` on `homelab-05` as kiosk user); reseat USB if the card is missing.

## Hard boundaries

- HA UI config-entry state (pipelines, Wyoming hosts, conversation agent selection, exposed entities) is observe-only. Report drift, do not silently overwrite it from Git.
- Do not rename the canonical entity IDs or `CLIENT_NAME` (`Homelab 05 Satellite`); renaming re-derives every entity prefix and breaks face, dashboard, and automations.
- When adding devices, expose only the control entity to the conversation agent, never diagnostic sensors.
- Assist traces (`processed_locally`) are routing ground truth, not latency metrics.

## Verify

After a fix: `just jarvis-eval-live` (live pipeline routing; local device actions still execute, so run it when someone is home). Offline corpus consistency: `tests/test_jarvis_voice_eval.py`.

## Maintaining this skill

This file is a living doc. When a debug session finds a new recurring symptom, a changed entity ID, or a boundary that moved, edit this file in the same session: add one row to the symptom table or update the chain, one or two lines each. Full procedures and prompt text belong in `docs/runbooks/jarvis-voice.md`; this file holds the debug path only. Verify doc edits with `just fmt-check`.
