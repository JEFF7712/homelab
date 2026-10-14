# Agent Task: pod-backup-age-executable

Status: `complete`

Base commit: `7e36f25472a163997f1cc0783afd0f6d27d27183`

Checkpoint HEAD: `7e36f25472a163997f1cc0783afd0f6d27d27183`

Owner: `codex`

Session: `sovereignty-closeout-20261012`

Exported at: `2026-10-13T17:01:47.415393+00:00`

Current HEAD at export: `7e36f25472a163997f1cc0783afd0f6d27d27183`

## Objective

Repair PodAgent backup executable before exposed-key rotation

## Acceptance criteria

- [x] Merged immutable policy preserves executable retained system binaries (PR176; homelab04 closure30f06688; policyz2y37jd active; scopedfleetdryrun andapply succeeded)
- [x] Pod Agent release is promoted and encrypted backup restores successfully (Woodpecker release 60; PR180 merged; three Deployments Ready on a67755b6; successful encrypted backup and off-host restore with integrity, SHA-256 and data counts matched. Recovery receipts: /home/rupan/sovereign-recovery/pod-agent/2026-10-13.)

## Owned source

- `scripts/ci/container_release.py`
- `tests/test_container_release.py`

## Remaining work

- None

## Verification

Current source fingerprint at export: `d3f665db553fafd66b5807d6da9fdb3bb25ab8139bc407de4ea4ef35e9c4c95f`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- None recorded

## Next action

None, executable repair and recovery proof complete

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
