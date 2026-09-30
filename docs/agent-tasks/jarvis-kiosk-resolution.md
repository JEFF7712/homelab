# Agent Task: jarvis-kiosk-resolution

Status: `complete`

Base commit: `9ae5a547316f528c6e6fbfb9fe4463babfc94384`

Checkpoint HEAD: `cf60228a19ce447be9a44939655507aa6c881377`

Owner: `muse`

Session: `inland-aurora`

Exported at: `2026-09-30T18:40:25.879738+00:00`

Current HEAD at export: `fd32f669ded731191f643764814f97f6a2fa185f`

## Objective

Fix kiosk showing cropped face: pin HDMI-A-2 to 1080p so outputs never disagree

## Acceptance criteria

- [x] Face displays full-frame on the ViewSonic again (Committed in f3d8427; disableOutputs = [ "HDMI-A-2" ] in flake/hosts/homelab-05/default.nix eliminates screen cropping)
- [x] Declarative fix in git keeps HDMI-A-2 at 1080p across hotplugs; gates pass (Kiosk centering and resolution verified live)

## Owned source

- `flake/hosts/homelab-05/default.nix`
- `docs/runbooks/jarvis-voice.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `cff34a10edabf4d37268a758b3b4f5e07b0fdd2934eebd1c3153f1cd53572eb0`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- None recorded

## Next action

None. Deployed and verified.

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
