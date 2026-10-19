# Agent Task: projectctl-integrations

Status: `complete`

Base commit: `096a4e6a6a0417c1ff877ab3b652a0992f847c70`

Checkpoint HEAD: `096a4e6a6a0417c1ff877ab3b652a0992f847c70`

Owner: `codex`

Session: `projectctl-integrations-2026-10-15`

Exported at: `2026-10-16T23:41:20.823015+00:00`

Current HEAD at export: `096a4e6a6a0417c1ff877ab3b652a0992f847c70`

## Objective

Complete projectctl CLI, trusted authority and service adapters, protected deployment packaging, and end-to-end local verification; identify concrete live prerequisites.

## Acceptance criteria

- [x] Human/agent CLI uses one core for adopt, plan, request, status, doctor and cancel; operator dispatch and signing remain separate. (/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/full-uignucgp.log; tests/test_projectctl.py, tests/test_projectctl_integrations.py, tests/test_projectctl_module.py, tests/test_development_ci.py)
- [x] Trusted authority fetches current protected approvals and verifies exact allowed registry transitions; supported API adapters fail closed on identity, visibility, policy, credentials and uncertain outcomes. (/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/full-uignucgp.log; tests/test_projectctl.py, tests/test_projectctl_integrations.py, tests/test_projectctl_module.py, tests/test_development_ci.py)
- [x] End-to-end local API tests exercise real HTTP adapters and gateway through onboarding, failure, interruption and recovery paths. (/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/full-uignucgp.log; tests/test_projectctl.py, tests/test_projectctl_integrations.py, tests/test_projectctl_module.py, tests/test_development_ci.py)
- [x] Packaging and source validation pass; concrete missing deployment identities, keys, authorization and pilot selection are documented without invented enrollment. (/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/full-uignucgp.log; tests/test_projectctl.py, tests/test_projectctl_integrations.py, tests/test_projectctl_module.py, tests/test_development_ci.py; .agent-state/evidence/checks/verify-4u6e0rvo.log; .agent-state/projectctl-package-verification.json; docs/runbooks/projectctl.md)

## Owned source

- `scripts/projectctl/`
- `tests/test_projectctl.py`
- `tests/test_projectctl_integrations.py`
- `tests/fixtures/projectctl/`
- `flake/modules/projectctl.nix`
- `flake/packages/projectctl.nix`
- `flake/flake.nix`
- `docs/runbooks/projectctl.md`
- `docs/agent-tasks/projectctl-integrations.md`
- `AGENT_MAP.md`
- `.sops.yaml`
- `config/projectctl/`
- `scripts/ci/development_projects.py`
- `scripts/ci/migrate_project.py`
- `flake/modules/woodpecker.nix`
- `flake/profiles/projectctl-pilot.nix`
- `scripts/checks/python.sh`
- `tests/test_projectctl_module.py`
- `tests/test_development_ci.py`
- `scripts/registry/core.py`
- `tests/test_registry_supply.py`
- `docs/runbooks/local-registry.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `567fa5db5a2a6135a2147c8e792d212f4f49ae4685f6e5be7b489e7bfa573a90`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `just check-changed`: exit 0, freshness `stale`, superseded `true`, verified at `2026-10-16T23:32:10.306382+00:00`, source fingerprint `3bf804e90378bf3e3815342ad3e80fc9fd5f02d374fcee94d5a6991d66a30f16`, evidence `/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-gnmeydcs.log`
- `just fmt-check`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-16T23:32:34.468309+00:00`, source fingerprint `3bf804e90378bf3e3815342ad3e80fc9fd5f02d374fcee94d5a6991d66a30f16`, evidence `/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-_8vvucn4.log`
- `nix build 'path:.?dir=flake#projectctl' --no-link --print-out-paths`: exit 0, freshness `current`, superseded `false`, verified at `2026-10-16T23:35:39.768515+00:00`, source fingerprint `567fa5db5a2a6135a2147c8e792d212f4f49ae4685f6e5be7b489e7bfa573a90`, evidence `/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-4u6e0rvo.log`
- `just check-changed`: exit 0, freshness `current`, superseded `false`, verified at `2026-10-16T23:40:04.973635+00:00`, source fingerprint `567fa5db5a2a6135a2147c8e792d212f4f49ae4685f6e5be7b489e7bfa573a90`, evidence `/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-v21fw023.log`

## Next action

Independent trusted operator follows docs/runbooks/projectctl.md: provision and verify the five dedicated credentials and new caller identity, rotate the exposed legacy registry credential, review transport/deployment changes, and only then authorize live autocompress enrollment. No publication, activation or enrollment has occurred.

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
