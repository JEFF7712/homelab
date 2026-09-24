# Agent Task: bose-bedroom-satellite

Status: `complete`

Base commit: `e1cfee3e7bfe618b793c859323509d117e743368`

Checkpoint HEAD: `e1cfee3e7bfe618b793c859323509d117e743368`

Owner: `Antigravity`

Session: `d4e58774-ddc5-4523-b2a3-7d94599370e6`

Exported at: `2026-09-24T17:22:33.263451+00:00`

Current HEAD at export: `4da5eb98c1af48f6c0541062f0c2105a5fd4123e`

## Objective

Set up Bose SoundLink Flex 2 Bluetooth audio sink and Wyoming satellite on nas-01

## Acceptance criteria

- [x] nas-01 has Bluetooth and PipeWire enabled in NixOS (Services bluetooth, pipewire, wireplumber, and wyoming-satellite active on nas-01; port 10700 verified reachable from Home Assistant pod)
- [x] Bose SoundLink Flex 2 paired via bluetoothctl on nas-01 (Device E4:58:BC:10:CA:C9 paired, bonded, trusted, and connected via Bluetooth A2DP with 100% battery)
- [x] wyoming-satellite audio sink runs on nas-01 (wyoming-satellite.service active and ready on tcp://0.0.30.20:10700)
- [x] Wyoming integration configured in Home Assistant and adopted into Git (Adopted home-assistant/integrations/wyoming_01M38Y12.yaml; audio playback verified in Music Assistant)

## Owned source

- `flake/hosts/nas-01/default.nix`
- `home-assistant/integrations/wyoming_01M38Y12.yaml`

## Remaining work

- None

## Verification

- None recorded

## Next action

None

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
