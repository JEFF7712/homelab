# Agent Task: bookshelf-release-lane

Status: `active`

Base commit: `de1c3661de34ad7d52f5f612fa8036e6f5e67f92`

Checkpoint HEAD: `2e6dcc939efd71bc3e35817d338d93c4e54d818d`

Owner: `codex`

Session: `sovereignty-closeout-20261012`

Exported at: `2026-10-13T19:18:34.174542+00:00`

Current HEAD at export: `2e6dcc939efd71bc3e35817d338d93c4e54d818d`

## Objective

CompleteBookshelfautomatedsovereignreleaseandrealstatebackuprestore

## Acceptance criteria

- [x] Clean retained-input build succeeds without networking and runtime returns HTTP200 (/persist/bookshelf-offline-drill-20261012/build-v3.log; --network=none; uid1000 pinnedpython runtime HTTP200;110focusedtests)
- [ ] Server-owned Woodpecker release is promoted and replacement is live (Compiler/catalog/sourcecontractprepared; pendingprotectedPRs)
- [x] Production state is restored from encrypted backup in isolation (bookshelf-state-backup complete; snapshot27ff6b38 restored offsite, hashes/integrity/table counts match, actual appHTTP200 with385books.)

## Owned source

- `scripts/ci/dotnet_release.py`
- `scripts/ci/applications.py`
- `scripts/ci/dotnet_service.py`
- `scripts/ci/dockerfile_supply.py`
- `config/ci/applications.json`
- `flake/modules/woodpecker.nix`
- `tests/test_dotnet_release.py`
- `tests/test_container_release.py`
- `services/bookshelf/README.md`

## Remaining work

- Merge and deploy PR201 when resumed
- New release preserving executable modes; isolated runtime test, protected promotion and live acceptance
- Retire legacy publisher after replacement acceptance

## Verification

Current source fingerprint at export: `b0fae0deb6ff9fc5c102a1fcfd300098d85c7cd5e48263a6930372e862d696fa`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- None recorded

## Next action

Deferred by user on 2026-10-13. Resume PR201 executable-mode repair, then scoped policy deployment, new release and isolated restored-state runtime acceptance before promotion.

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
