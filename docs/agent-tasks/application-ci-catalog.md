# Agent Task: application-ci-catalog

Status: `complete`

Base commit: `840636cd9fe92da13ad25bc4108fa78adbdd11ae`

Checkpoint HEAD: `840636cd9fe92da13ad25bc4108fa78adbdd11ae`

Owner: `codex`

Session: `application-ci-catalog-20261008`

Exported at: `2026-10-08T17:52:04.350909+00:00`

Current HEAD at export: `840636cd9fe92da13ad25bc4108fa78adbdd11ae`

## Objective

Implement a server-owned application catalog and isolated Woodpecker validation policy for the Darkbit pilot

## Acceptance criteria

- [x] Catalog validation fails closed and application requests cannot enter infrastructure operations (tests/test_application_ci.py rejects identities, events, variables and repository-selected commands before infrastructure API access)
- [x] Darkbit source validates through an immutable secret-free workflow with explicit enrollment (.agent-state/evidence/checks/verify-r5_4a4is.log: 40 tests pass; static validator accepts actual Darkbit checkout; catalog null enrollment denies execution)
- [x] Existing homelab CI tests pass, service wiring evaluates and scoped checks are recorded (.agent-state/evidence/checks/verify-v7tbekn2.log records nonstale full selection; nix-agent path flake evaluation and dry-build pass; baseline failures explicitly documented)

## Owned source

- `config/ci/applications.json`
- `scripts/ci/applications.py`
- `scripts/ci/static_site.py`
- `scripts/ci/policy.py`
- `tests/test_application_ci.py`
- `flake/modules/woodpecker.nix`
- `docs/runbooks/application-ci.md`
- `docs/agent-tasks/application-ci-catalog.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `3dd34c56a3dc62c2b1b2f4527514352da33668568544d0fd7f0e707a4e82ca4a`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `nix develop ./flake -c just check-changed`: exit 1, freshness `stale`, superseded `true`, verified at `2026-10-08T17:48:55.666069+00:00`, source fingerprint `e7b4dc0c78003969a51c721056f878bfd183d4ebb7684bcc3c072cced973d3c3`, evidence `/home/rupan/homelab/.agent-state/evidence/checks/verify-nggd7535.log`
- `nix develop ./flake -c python -m unittest tests.test_application_ci tests.test_forgejo_ci`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-08T17:50:17.243344+00:00`, source fingerprint `167d9f150dafe233552b497c48820b9b431b15820106e8f0846b01ab09b421a1`, evidence `/home/rupan/homelab/.agent-state/evidence/checks/verify-r5_4a4is.log`
- `nix develop ./flake -c just check-changed`: exit 1, freshness `stale`, superseded `false`, verified at `2026-10-08T17:51:31.884033+00:00`, source fingerprint `167d9f150dafe233552b497c48820b9b431b15820106e8f0846b01ab09b421a1`, evidence `/home/rupan/homelab/.agent-state/evidence/checks/verify-v7tbekn2.log`

## Next action

Obtain authorization for publishing and live Forgejo/Woodpecker migration; build/publication and offline recovery remain future stages

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
