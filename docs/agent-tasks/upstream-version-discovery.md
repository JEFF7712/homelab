# Agent Task: upstream-version-discovery

Status: `complete`

Base commit: `036eac52aefd36d5d8ee203d25ac2acc5409bb57`

Checkpoint HEAD: `036eac52aefd36d5d8ee203d25ac2acc5409bb57`

Owner: `antigravity`

Session: `upstream-version-discovery-session`

Exported at: `2026-09-30T17:33:46.872446+00:00`

Current HEAD at export: `036eac52aefd36d5d8ee203d25ac2acc5409bb57`

## Objective

Implement semantic upstream version discovery and decoupled advisory/CVE reporting with non-automerge MR policy.

## Acceptance criteria

- [x] Semantic tag parser and comparison handles semver tags (with or without 'v' prefix, patch/minor/major, flavor suffixes) (tests/test_registry_supply.py:SemVerTests)
- [x] Upstream version discovery queries source repositories and identifies candidate patch, minor, and major updates (tests/test_registry_supply.py:UpstreamDiscoveryTests)
- [x] scripts.registry CLI exposes 'discover-upstream' command emitting structured update reports (tests/test_registry_supply.py:RegistryCliUpstreamAndCveTests.test_cli_discover_upstream)
- [x] renovate.json explicitly disables automerging for container image updates across GitOps to preserve the manual import gate (renovate.json packageRules for flux, kubernetes, dockerfile, gitlabci with matchDatasources docker)
- [x] Vulnerability scanning is cleanly decoupled from image copy/verify, with distinct handling for scanner network/DB failures vs vulnerability findings (tests/test_registry_supply.py:CveAuditTests)
- [x] Unit tests cover semver parsing, candidate selection, flavor matching, report generation, and scanner state classification (python -m unittest tests.test_registry_supply passed 140 tests)
- [x] Remediation 1: Digest-free/unbound scan evidence cannot validate pinned images; verified digest association is required (tests/test_registry_supply.py:RegistryReviewRegressionTests.test_p1_unbound_scan_without_digest_cannot_establish_success)
- [x] Remediation 1b: Multiple lock digests under the same repository strictly validate against their respective digests (tests/test_registry_supply.py:RegistryReviewRegressionTests.test_p1_two_lock_digests_under_one_repository)
- [x] Remediation 2: Nested findings and explicit failure states are strictly validated in Trivy and normalized payloads (tests/test_registry_supply.py:RegistryReviewRegressionTests.test_p1_nested_findings_and_scanner_states_strictly_validated)
- [x] Remediation 3: Successful baseline findings are preserved through arbitrarily many outages, and genuine resolutions reported upon recovery (tests/test_registry_supply.py:RegistryReviewRegressionTests.test_p2_outage_recovery_preserves_successful_baseline)
- [x] Remediation 4: SemVer 2.0 Item 11 compliance with lexical ASCII ordering for alphanumeric identifiers and compound dot sequence parsing (tests/test_registry_supply.py:RegistryReviewRegressionTests.test_p2_semver_item_11_compliance)
- [x] Remediation 5: Reject malformed Trivy targets without Target string or with non-list Vulnerabilities (null); accept omitted or empty Vulnerabilities on valid targets (tests/test_registry_supply.py:RegistryReviewRegressionTests.test_p1_trivy_null_vulnerabilities_and_missing_target_rejected)
- [x] Remediation 6: Do not synthesize clean baselines from unknown deltas or missing scan results; carry forward only observed successful evidence (tests/test_registry_supply.py:RegistryReviewRegressionTests.test_p2_missing_scans_do_not_manufacture_successful_baseline)

## Owned source

- `scripts/registry/core.py`
- `scripts/registry/cli.py`
- `renovate.json`
- `tests/test_registry_supply.py`
- `docs/runbooks/local-registry.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `dd8b4cda26a320aef6642735131f87d6bb6179cc4fb7ad775c4a2c15db043c6c`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `python -m unittest tests.test_registry_supply`: exit 0, freshness `stale`, superseded `true`, verified at `2026-09-30T17:03:10.870026+00:00`, source fingerprint `5976b78cee322bee853485d430109c97c5a92f1105d1a6018d42bb94dc1e5b93`, evidence `/home/rupan/homelab/.agent-state/evidence/checks/verify-2ndgshve.log`
- `python -m unittest tests.test_registry_supply`: exit 0, freshness `stale`, superseded `false`, verified at `2026-09-30T17:15:34.505430+00:00`, source fingerprint `698bc25c82042fb0f8e62d0d724d3b804201dc24e507417bfea112a562b16420`, evidence `/home/rupan/homelab/.agent-state/evidence/checks/verify-y7cdqash.log`
- `python -m unittest tests.test_registry_supply tests.test_k3s_peer_caching tests.test_zot_fast_restart`: exit 0, freshness `stale`, superseded `false`, verified at `2026-09-30T17:16:08.324600+00:00`, source fingerprint `078c2a38e77ba52f433963c5e433593e7a35c21cd1df775a9159fe43acb75509`, evidence `/home/rupan/homelab/.agent-state/evidence/checks/verify-g2emm3zo.log`

## Next action

Auditor review ready

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
