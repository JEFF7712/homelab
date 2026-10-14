# Agent Task: sovereign-ci-source-retention

Status: `complete`

Base commit: `055aaf6a2f059bac33208416cf0f0758f4250473`

Checkpoint HEAD: `f19999912b4404822dd251bd8a25f1f01116b7de`

Owner: `codex`

Session: `sovereign-ci-inputs-20261008`

Exported at: `2026-10-09T04:33:02.316586+00:00`

Current HEAD at export: `f19999912b4404822dd251bd8a25f1f01116b7de`

## Objective

Retain every recursive pinned Nix flake source in the existing local CI cache and verify availability

## Acceptance criteria

- [x] Every recursive archived source is included in publication roots; malformed archives fail before publication (.agent-state/evidence/checks/verify-clkt2syt.json; 3 focused behavioral/integration tests)
- [x] Protected CI publishes missing sources and all recursive external sources have local narinfo (.agent-state/evidence/nix-source-retention-after.json; pipeline718 cache-publish success)
- [x] Documentation distinguishes source retention from clean offline CI and independent recovery (docs/runbooks/local-development.md protected PR115 merged9130256)

## Owned source

- `scripts/ci/cache.sh`
- `scripts/ci/cache_sources.py`
- `tests/test_ci_cache_sources.py`
- `docs/runbooks/local-development.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `82fa0446e97642a5cf826b833203ccc23cbcf98a2d70ed714e6964c2a93d4fb1`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `just check-changed`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-09T04:22:19.192398+00:00`, source fingerprint `86ab9c5e256179be89eae66f71a1e01ec3e8f353131c083e05c8b0286b197d7a`, evidence `/home/rupan/homelab/.worktrees/sovereign-ci-source-retention/.agent-state/evidence/checks/verify-clkt2syt.log`

## Next action

Complete; clean source-bootstrap/full-CI tests remain separate work

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
