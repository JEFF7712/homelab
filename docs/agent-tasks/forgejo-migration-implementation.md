# Agent Task: forgejo-migration-implementation

Status: `complete`

Base commit: `1da0c7c7631552cd54da98c47dbbfba12059a57f`

Checkpoint HEAD: `397448f0e529ca6686d78353392b245236782c41`

Owner: `codex`

Session: `forgejo-migration-implementation-2026-10-03`

Exported at: `2026-10-04T09:33:47.587421+00:00`

Current HEAD at export: `397448f0e529ca6686d78353392b245236782c41`

## Objective

Complete source implementation of Forgejo migration with isolated CI, full operation parity, state/backups, mirrors and explicit recoverable fallback

## Acceptance criteria

- [x] Server-owned CI policy isolates untrusted code and preserves validation coverage (Native Woodpecker 3.18 strict lint and httpsign v0.5.2 interop; tests/test_forgejo_ci.py)
- [x] Manual operations preserve legacy behavior, plans and serialization (35 catalog targets and tests/test_forgejo_automation.py including GitLab role/file mapping and review-branch publication)
- [x] State, platform backup, secret ownership and Git fallback are implemented and tested (Native OpenTofu HTTPS locking/authority integration, SQLite restore, immutable artifacts and divergent Git mirror regressions)
- [x] Affected builds, regression tests and full offline gate pass (verify-_kqgu8ea.log: full offline exit 0, 1315 tests, 35 skips; four successful NixOS builds; focused formatter pass)
- [x] Concrete deployment and recovery handoff prepared for authorization (docs/runbooks/forgejo-disaster-recovery.md and protected API payloads in config/ci)

## Owned source

- `flake/modules/woodpecker.nix`
- `scripts/ci`
- `tests`
- `config/ci`
- `.woodpecker`
- `flake/modules/forgejo.nix`
- `flake/modules/nas-s3.nix`
- `flake/modules/nas-data.nix`
- `tofu`
- `docs/runbooks`
- `gitops/automation`
- `gitops/eso`

## Remaining work

- None

## Verification

Current source fingerprint at export: `a580287750399088d87f7455b38a56c8e18ee706f6fa30a248bf4c64e7b86906`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `just check`: exit 1, freshness `stale`, superseded `true`, verified at `2026-10-04T09:13:29.901493+00:00`, source fingerprint `c3607c67d0c3c43dade66c99a64603450e75eb1560cc322c632c974ece13b14b`, evidence `/tmp/homelab-forgejo-final/.agent-state/evidence/checks/verify-9g8m0muz.log`
- `just check`: exit 69, freshness `stale`, superseded `true`, verified at `2026-10-04T09:15:40.041769+00:00`, source fingerprint `b8b5ef6bbe9c746fe43cc1b5b3daf7e8b23ae691e73d33a5a5684a88512cd00a`, evidence `/tmp/homelab-forgejo-final/.agent-state/evidence/checks/verify-n2wjy4c0.log`
- `just check`: exit 1, freshness `stale`, superseded `true`, verified at `2026-10-04T09:18:28.399841+00:00`, source fingerprint `b8b5ef6bbe9c746fe43cc1b5b3daf7e8b23ae691e73d33a5a5684a88512cd00a`, evidence `/tmp/homelab-forgejo-final/.agent-state/evidence/checks/verify-uowggqug.log`
- `just check`: exit 0, freshness `stale`, superseded `true`, verified at `2026-10-04T09:22:04.366021+00:00`, source fingerprint `e7ebc1acd7f49008e7c92c18609734ddfcf97e3bf7679726cc770990d3c6d5d3`, evidence `/tmp/homelab-forgejo-final/.agent-state/evidence/checks/verify-cmj4nf5o.log`
- `just check`: exit 0, freshness `stale`, superseded `true`, verified at `2026-10-04T09:25:10.465844+00:00`, source fingerprint `c25ed0a06e140808315d3048651aac5e558e9a17239807005ccbfab307c68326`, evidence `/tmp/homelab-forgejo-final/.agent-state/evidence/checks/verify-qecrrbuo.log`
- `just check`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-04T09:30:19.688091+00:00`, source fingerprint `bdd92824873c5dcddd2c8f6afa1dccafa0417681c1a8a2d30e0dafa4e218cb35`, evidence `/tmp/homelab-forgejo-final/.agent-state/evidence/checks/verify-_kqgu8ea.log`

## Next action

Obtain shared-infrastructure authorization, then execute docs/runbooks/forgejo-disaster-recovery.md including fresh credentials, encrypted secret regeneration, publication, deployment and live acceptance

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
