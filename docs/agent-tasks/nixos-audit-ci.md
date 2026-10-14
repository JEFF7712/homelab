# Agent Task: nixos-audit-ci

Status: `complete`

Base commit: `691ce5d67378ec85a29227d0a9899c6f1dab72ae`

Checkpoint HEAD: `a8d176e56c745c0061ea9f0f5300370ecd8814e4`

Owner: `Codex root`

Session: `nixos-audit-2026-10-11`

Exported at: `2026-10-11T21:30:50.642967+00:00`

Current HEAD at export: `a8d176e56c745c0061ea9f0f5300370ecd8814e4`

## Objective

Restrict public nixos-config GitHub runners to trusted main artifact workflows.

## Acceptance criteria

- [x] The runner guard admits only approved main artifact events and rejects PR, fork, branch, and workflow mismatches. (/home/rupan/homelab/.worktrees/nixos-audit-ci/.agent-state/evidence/checks/verify-2vurzjtf.log)
- [x] The guard is deployed to homelab-04 and verified before publishing the hosted validation workflows. (/home/rupan/homelab/.worktrees/nixos-audit-ci/.agent-state/evidence/checks/verify-9ube8qku.log)

## Owned source

- `flake/modules/github-runner-nixos.nix`
- `flake/modules/github-runner-nixos/job-guard.sh`
- `tests/test_github_runner_nixos_guard.py`
- `docs/runbooks/ci-pipelines.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `bd5b6057361407747b1d4440639bb0530061c2f0a588ae6a76471ec814d774bb`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `python3 -m unittest discover -s tests -p test_github_runner_nixos_guard.py`: exit 0, freshness `stale`, superseded `true`, verified at `2026-10-11T20:38:46.716551+00:00`, source fingerprint `6c4b64eab1363e772b4cc7769ac2ab3d9fa9f0d1c72da889577dba518cf588c4`, evidence `/home/rupan/homelab/.worktrees/nixos-audit-ci/.agent-state/evidence/checks/verify-ldm309m1.log`
- `env NIXOS_CI_JOB_GUARD=/nix/store/4819imj1rkw3fbwpx4y9bhmng319jf5q-nixos-ci-job-guard.sh python3 -m unittest discover -s tests -p test_github_runner_nixos_guard.py`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-11T20:41:20.272971+00:00`, source fingerprint `6c4b64eab1363e772b4cc7769ac2ab3d9fa9f0d1c72da889577dba518cf588c4`, evidence `/home/rupan/homelab/.worktrees/nixos-audit-ci/.agent-state/evidence/checks/verify-2vurzjtf.log`
- `python3 -m unittest discover -s tests -p test_github_runner_nixos_guard.py`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-11T21:10:12.360357+00:00`, source fingerprint `506f4cb4b64b89b667853bc24748b0348fcf8c441b3eca93b1df48d207a781b9`, evidence `/home/rupan/homelab/.worktrees/nixos-audit-ci/.agent-state/evidence/checks/verify-dstaahzh.log`
- `bash /tmp/nixos-audit-ci-live-check.sh`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-11T21:11:05.155729+00:00`, source fingerprint `506f4cb4b64b89b667853bc24748b0348fcf8c441b3eca93b1df48d207a781b9`, evidence `/home/rupan/homelab/.worktrees/nixos-audit-ci/.agent-state/evidence/checks/verify-9ube8qku.log`
- `python3 /tmp/nixos-audit-ci-acceptance.py`: exit 0, freshness `current`, superseded `false`, verified at `2026-10-11T21:29:32.483094+00:00`, source fingerprint `bd5b6057361407747b1d4440639bb0530061c2f0a588ae6a76471ec814d774bb`, evidence `/home/rupan/homelab/.worktrees/nixos-audit-ci/.agent-state/evidence/checks/verify-i5rzxn6o.log`

## Next action

Complete; artifact builds continue under GitHub Actions.

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
