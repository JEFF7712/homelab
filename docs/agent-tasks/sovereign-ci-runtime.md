# Agent Task: sovereign-ci-runtime

Status: `complete`

Base commit: `2f72bfe9e7363028a743bd6f77b1d5fee2960eef`

Checkpoint HEAD: `f4be43de2bb82618dff027b92c9010c942a52653`

Owner: `codex`

Session: `sovereign-ci-runtime-20261008`

Exported at: `2026-10-09T04:44:17.954854+00:00`

Current HEAD at export: `f4be43de2bb82618dff027b92c9010c942a52653`

## Objective

Remove the Docker Hub runtime dependency from server-owned homelab CI using the verified retained Nix image

## Acceptance criteria

- [x] Server-owned Nix runtime references retained Zot image with unchanged reviewed digest (Protected PR116 merged89524ef; verified independent Zot runtime OCI copy)
- [x] Fresh post-activation homelab validation succeeds with local runtime image (.agent-state/evidence/runtime-live-acceptance.json; pipeline724 success; observed container image is pinned Zot runtime)

## Owned source

- `scripts/ci/policy.py`
- `docs/runbooks/application-ci.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `1afca18a56c5211c022f54b3463205eb5bcad791ce7859c8b004ad5f96e61929`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `just check-changed`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-09T04:25:25.093645+00:00`, source fingerprint `2141881b2ed76b5bab6f95597dca2d9cb6e9b41324fe43ea2c1aafe1cfd07442`, evidence `/home/rupan/homelab/.worktrees/sovereign-ci-runtime/.agent-state/evidence/checks/verify-v4iox7va.log`
- `just check-changed origin/main`: exit 0, freshness `current`, superseded `false`, verified at `2026-10-09T04:30:01.493978+00:00`, source fingerprint `1afca18a56c5211c022f54b3463205eb5bcad791ce7859c8b004ad5f96e61929`, evidence `/home/rupan/homelab/.worktrees/sovereign-ci-runtime/.agent-state/evidence/checks/verify-be_2j6nm.log`

## Next action

Complete; remaining build-environment hooks, provider/schema retention and full offline CI proof are separate work

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
