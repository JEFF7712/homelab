# Agent Task: post-migration-cleanup

Status: `complete`

Base commit: `1ba33e9bf11c572dd68f8392c1a538cc1ee90357`

Checkpoint HEAD: `18646d82d2c16b5e79e85d8cf5a8f4ff318639b0`

Owner: `codex`

Session: `post-migration-cleanup-20261011`

Exported at: `2026-10-12T02:38:23.487700+00:00`

Current HEAD at export: `18646d82d2c16b5e79e85d8cf5a8f4ff318639b0`

## Objective

Complete authorized post-migration credential, Pages, and runner cleanup

## Acceptance criteria

- [x] Exposed and one-time tokens revoked; replacement tea token works (Forgejo rows 15 and 26 absent; package token HTTP401; replacement 27 authenticated)
- [x] Orphaned Pages projects deleted; public sites healthy (/home/rupan/homelab/.agent-state/migration-cleanup/pages-deletion.json)
- [x] 12 idle migrated runners scaled to zero through protected GitOps (/home/rupan/homelab/.agent-state/migration-cleanup/runner-live-acceptance.json)

## Owned source

- `gitops/automation/github-runner/deployment.yaml`
- `docs/runbooks/application-ci.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `ba621aaf349873feff4a7b9156b945f6808810274273f550e74f0db5d77e854c`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `nix develop ./flake -c just check-changed`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-12T02:28:50.486789+00:00`, source fingerprint `3812837e00b82235ad5f82387d3ba17fb86b49623fd9e8505b85627d0a5ded2f`, evidence `/home/rupan/homelab/.worktrees/post-migration-cleanup/.agent-state/evidence/checks/verify-7j1_oetg.log`
- `python .agent-state/accept-cleanup.py`: exit 0, freshness `current`, superseded `false`, verified at `2026-10-12T02:37:52.288665+00:00`, source fingerprint `ba621aaf349873feff4a7b9156b945f6808810274273f550e74f0db5d77e854c`, evidence `/home/rupan/homelab/.worktrees/post-migration-cleanup/.agent-state/evidence/checks/verify-ht_z4_xs.log`

## Next action

No cleanup work remains; CR Demo release proof and Sovereign inquiry recovery stay with migration owners

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
