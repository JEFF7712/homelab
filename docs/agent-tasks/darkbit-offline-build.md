# Agent Task: darkbit-offline-build

Status: `complete`

Base commit: `1f553c7ebf9598bb15a36ca28c7b265fa9b752e9`

Checkpoint HEAD: `eb60db8dd3417ceff64706b9eeb334a85988f5ca`

Owner: `codex`

Session: `darkbit-offline-20261008`

Exported at: `2026-10-09T04:18:14.738390+00:00`

Current HEAD at export: `eb60db8dd3417ceff64706b9eeb334a85988f5ca`

## Objective

Retain and pin Woodpecker clone tooling locally and prove an independent cold offline Darkbit checkout and build

## Acceptance criteria

- [x] Woodpecker default clone plugin uses verified digest in Zot; live replacement checkout succeeds (.agent-state/evidence/clone-live-acceptance.json; .agent-state/evidence/clone-zot-retention.json)
- [x] Fresh disposable builder restores retained source using retained git tool, assembles production image with network disabled and matches accepted digest (/home/rupan/sovereign-recovery/darkbit/2026-10-08/cold-proof-20261008/receipt.json)
- [x] Document all remaining full-CI and platform-recovery dependencies without claiming unsupported sovereignty (docs/runbooks/application-ci.md commit8f3b9ad and PR114)

## Owned source

- `flake/modules/woodpecker.nix`
- `gitops/producer-base-images/base-images.yaml`
- `registry/images.inventory.json`
- `registry/images.lock.json`
- `docs/runbooks/application-ci.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `a367440a2aa03833d5971ff94c068dd33f56cb2a2d32e8dd95079b93a69e8504`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- None recorded

## Next action

Complete; next phase retains missing Nix CI source inputs in separate task

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
