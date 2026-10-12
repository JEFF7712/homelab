# Agent Task: sovereign-ci-build-inputs

Status: `complete`

Base commit: `89524ef7dc0bdd256ca9577fc57eab47e6d99d2e`

Checkpoint HEAD: `342f45fa98ddfcb9f62cbf8b18348b8f3f6944fd`

Owner: `codex`

Session: `sovereign-ci-build-inputs-20261008`

Exported at: `2026-10-09T13:51:29.339216+00:00`

Current HEAD at export: `342f45fa98ddfcb9f62cbf8b18348b8f3f6944fd`

## Objective

Retain the available development-shell build-input graph and verify replacement CI stops downloading observed helper inputs publicly

## Acceptance criteria

- [x] Publication includes available build inputs; graph enumeration failure prevents publication (verify-keiue9qe.json: full gate passed; tests cover graph enumeration failure preventing push; PR117 merged;cache-publication-acceptance.json confirms pipeline729 success and shell derivation retained)
- [x] All six observed public build helpers are retained locally and replacement CI does not download them publicly (helper-local-availability.json: six matching Attic paths with homelab signature metadata; replacement-ci-acceptance.json: pipeline731 passed,620 local Nix downloads,0 public,all6helpers observed locally; cold-build-inputs.json: fresh disconnected restore passed)
- [x] Remaining full offline CI provider/schema/recovery gates are documented (Merged local-development runbook describes retained sources, cold source bootstrap, local runtime, and remaining provider/schema/full recovery gates)

## Owned source

- `scripts/ci/cache.sh`
- `tests/test_ci_cache_sources.py`
- `docs/runbooks/local-development.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `892060b3983d23e92ff1ed42e3711cee833a05ca1cbfc564b6c877e208bc643b`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `just check-changed`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-09T04:59:15.795706+00:00`, source fingerprint `6bdb3122ca445b5030a098f92df71eb193404b3a815615468dbe0aff29c10094`, evidence `/home/rupan/homelab/.worktrees/sovereign-ci-build-inputs/.agent-state/evidence/checks/verify-keiue9qe.log`

## Next action

Extend available-input graph retention from development shell to all declared build roots

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
