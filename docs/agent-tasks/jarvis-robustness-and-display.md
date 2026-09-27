# Agent Task: jarvis-robustness-and-display

Status: `complete`

Base commit: `8354647511d2e816b47bb334638fc5a94bb72fa6`

Checkpoint HEAD: `d379cea764ff0c0b4e185321e28027743648f98a`

Owner: `Antigravity`

Session: `6e0388fe-a32e-43fd-bd7b-9f3f4da535d6`

Exported at: `2026-09-24T23:15:27.846373+00:00`

Current HEAD at export: `d379cea764ff0c0b4e185321e28027743648f98a`

## Objective

Prevent silent satellite microphone failure on USB disconnect and prevent kiosk face display offset

## Acceptance criteria

- [x] Satellite audio watchdog and healthcheck detect flatline silence / disconnected streams and fail within 10s (Implemented in gitops/voice/satellite/__main__.py and gitops/voice/satellite.yaml: tracks consecutive digital silence blocks (amplitude == 0) and triggers clean exit after 64 consecutive blocks (~4.1s) and deletes /tmp/satellite_audio_healthy. Tested by tests/test_satellite_recovery.py:test_audio_loop_detects_digital_silence_and_cleans_health_file passing.)
- [x] homelab-05 disables HDMI-A-2 kiosk output on startup to keep face canvas centered at 1920x1080 (Added disableOutputs = [ "HDMI-A-2" ]; to homelab.kiosk on homelab-05. Switched via deploy_fleet, verified via wlr-randr showing HDMI-A-2 Enabled: no and HDMI-A-1 Enabled: yes at 1920x1080.)
- [x] homelab-05 udev rule triggers satellite-alsa-restore on sound card add events (Added udev rule in flake/hosts/homelab-05/default.nix restarting satellite-alsa-restore.service on QuadCast USB add. Deployed via deploy_fleet, verified in /etc/udev/rules.d/99-local.rules.)

## Owned source

- `flake/hosts/homelab-05/default.nix`
- `gitops/voice/satellite.yaml`
- `gitops/voice/satellite/__main__.py`
- `gitops/voice/satellite/healthcheck.py`
- `tests/test_home_assistant_voice_satellite_config.py`
- `tests/test_satellite_recovery.py`

## Remaining work

- None

## Verification

- None recorded

## Next action

Handoff to user

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
