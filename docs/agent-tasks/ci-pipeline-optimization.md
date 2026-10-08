# Agent Task: ci-pipeline-optimization

Status: `active`

Base commit: `5074163d13ab3d2799f2b1c285ccae7874875967`

Checkpoint HEAD: `5074163d13ab3d2799f2b1c285ccae7874875967`

Owner: `antigravity`

Session: `12e30a28-9fcf-424c-88e2-a98d0e4c62d6`

Exported at: `2026-10-08T01:05:38.985435+00:00`

Current HEAD at export: `5074163d13ab3d2799f2b1c285ccae7874875967`

## Objective

Diagnose and implement fixes for slow Forgejo/Woodpecker CI: eliminate redundant validation, fix dependency cold starts, introduce safe change-based validation, and improve timing visibility.

## Acceptance criteria

- [x] Eliminate redundant validation: PR events do not rerun already valid checks for identical tested inputs or overwrite successful required status with pending, accounting for base changes, force pushes, and input changes. (find_reusable_validation implemented in policy.py returning pr-validation-reused.yaml without overwriting validation-v2. Tested with 27 unit tests in test_forgejo_ci.py.)
- [x] Fix dependency cold starts: local Attic cache warms and supplies validation environments without privileged/write credentials in sandbox jobs. (Attic push updated with --ignore-upstream-cache-filter in cache.sh and justfile; extra-substituters configured with ?priority=30 in policy.py and offline-checks.yaml.)
- [x] Introduce safe change-based validation: reuse/improve existing scope selection code to run scoped checks for narrow changes and fallback to broad checks for shared changes/locks, keeping required ci/woodpecker/validation-v2 status. (validate repository integrated into VALIDATION_COMMAND with scope classification (docs in ~2s, HA in ~15s, full gate for main/nix changes). Verified in test_ci_routing.py.)
- [x] Improve timing visibility: record queue time, environment provisioning, provider initialization, validation lanes, and significant expensive substeps. (run.py records queue_seconds from CI_PIPELINE_CREATED; timings_report.py reports [WAIT / QUEUE] separately from [EXECUTION BREAKDOWN].)
- [x] Comprehensive regression tests covering event deduplication, invalidation, scope fallback, and security boundaries. (tests/test_forgejo_ci.py and tests/test_ci_routing.py pass all tests covering deduplication, invalidation on fork/base change/force push, and scoping.)
- [x] Pass all offline gates (just check-changed, just check, just fmt-check) and prepare deployment and rollback procedures. (just check-changed (exit 0), just check (exit 0), just fmt-check (exit 0) all pass cleanly offline.)

## Owned source

- `.woodpecker/offline-checks.yaml`
- `config/ci/woodpecker-repository.json`
- `docs/runbooks/ci-pipelines.md`
- `justfile`
- `scripts/ci/cache.sh`
- `scripts/ci/policy.py`
- `scripts/ci/run.py`
- `scripts/ci/scope.py`
- `scripts/ci/timings_report.py`
- `scripts/ci/validate.py`
- `tests/test_forgejo_ci.py`

## Remaining work

- Obtain user authorization before deploying to shared infrastructure
- Deploy policy service to homelab-04 and populate Attic cache
- Perform live verification and benchmark

## Verification

Current source fingerprint at export: `b639ff6e063309d547f2125d2057d8620d66d33104d3698fc9413bbbb5dc73b0`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `bash scripts/checks/all.sh`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-08T01:01:04.223526+00:00`, source fingerprint `fe869a661d254e17293bf06c3ab63e9a24f154a06e21ce9ed0b14ab34b7d0d1a`, evidence `/home/rupan/homelab/.agent-state/evidence/checks/full-y5j2gc0v.log`
- `nix develop ./flake -c just fmt-check`: exit 0, freshness `current`, superseded `false`, verified at `2026-10-08T01:05:31.792358+00:00`, source fingerprint `b639ff6e063309d547f2125d2057d8620d66d33104d3698fc9413bbbb5dc73b0`, evidence `/home/rupan/homelab/.agent-state/evidence/checks/verify-2yza937j.log`

## Next action

Present findings, verification results, deployment and rollback plan to user for authorization

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
