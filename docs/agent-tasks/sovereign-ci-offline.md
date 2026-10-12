# Agent Task: sovereign-ci-offline

Status: `complete`

Base commit: `693af3505705cc6275f1f85c0c78d1b08e3150b8`

Checkpoint HEAD: `311028c998751399b245ea21c9a0f4c52f6f2287`

Owner: `codex`

Session: `sovereign-ci-offline-20261009`

Exported at: `2026-10-09T17:39:07.114954+00:00`

Current HEAD at export: `311028c998751399b245ea21c9a0f4c52f6f2287`

## Objective

Retain providers and core schemas and execute full CI offline on a clean builder

## Acceptance criteria

- [x] Locked OpenTofu providers install exclusively from retained inputs (/home/rupan/sovereign-recovery/homelab-ci/2026-10-09-offline/proof-006/receipt.json; /home/rupan/sovereign-recovery/homelab-ci/2026-10-09-offline/proof-006/negative-provider-receipt.json)
- [x] Core Kubernetes schemas validate locally and missing schemas fail (/home/rupan/sovereign-recovery/homelab-ci/2026-10-09-offline/proof-006/receipt.json)
- [x] Clean network-isolated builder executes full CI checks successfully (/home/rupan/sovereign-recovery/homelab-ci/2026-10-09-offline/proof-006/receipt.json)

## Owned source

- `nix/ci-providers.nix`
- `nix/ci-providers.json`
- `flake/flake.nix`
- `scripts/checks/gitops.sh`
- `schemas/kubernetes-core`
- `tests/test_ci_offline_inputs.py`
- `docs/runbooks/local-development.md`
- `justfile`
- `scripts/ci/offline.py`
- `tests/test_ci_checks.py`
- `.gitleaksignore`

## Remaining work

- None

## Verification

Current source fingerprint at export: `59f96a471c0173b3b9bb5f7c3b63015a9d8073141e960a2769b26b95b99b9f51`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `just check-changed`: exit 1, freshness `stale`, superseded `true`, verified at `2026-10-09T15:06:13.244024+00:00`, source fingerprint `3f415941cc6dbfc2f606f209dc708cc908ead7c2702abc827ee09ee69961f765`, evidence `/home/rupan/homelab/.worktrees/sovereign-ci-offline/.agent-state/evidence/checks/verify-mynlqus8.log`
- `just check-changed`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-09T15:12:47.849642+00:00`, source fingerprint `070a0b37a49ae335e200e49218bf6e9386a1337da8bdda361fc53145224018cf`, evidence `/home/rupan/homelab/.worktrees/sovereign-ci-offline/.agent-state/evidence/checks/verify-lpj2eq52.log`
- `python .agent-state/accept-offline.py`: exit 1, freshness `current`, superseded `true`, verified at `2026-10-09T17:33:53.238748+00:00`, source fingerprint `59f96a471c0173b3b9bb5f7c3b63015a9d8073141e960a2769b26b95b99b9f51`, evidence `/home/rupan/homelab/.worktrees/sovereign-ci-offline/.agent-state/evidence/checks/verify-9ea737qk.log`
- `python .agent-state/accept-offline.py`: exit 0, freshness `current`, superseded `false`, verified at `2026-10-09T17:34:09.379212+00:00`, source fingerprint `59f96a471c0173b3b9bb5f7c3b63015a9d8073141e960a2769b26b95b99b9f51`, evidence `/home/rupan/homelab/.worktrees/sovereign-ci-offline/.agent-state/evidence/checks/verify-8nbwblof.log`

## Next action

No further application migrations in this task

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
