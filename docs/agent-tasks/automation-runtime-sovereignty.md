# Agent Task: automation-runtime-sovereignty

Status: `complete`

Base commit: `350817e519fbbd522178c3dbc45fdaf0acef729d`

Checkpoint HEAD: `350817e519fbbd522178c3dbc45fdaf0acef729d`

Owner: `codex`

Session: `sovereignty-closeout-20261013`

Exported at: `2026-10-13T18:29:58.123653+00:00`

Current HEAD at export: `350817e519fbbd522178c3dbc45fdaf0acef729d`

## Objective

Remove legacy automation image-producer dependencies while preserving local control and approval boundaries

## Acceptance criteria

- [x] Automation consumers no longer require legacy custom-image producers (PR194 merged; Flux automation Ready; dashboard1/1 on retained Python c6ead215; reviewer CronJob pinned to retained Renovate97006d85; both script sources remain Forgejo-authoritative ConfigMaps.)
- [x] Retained runtimes support actual application code and local Forgejo backend (Network-disabled import/version/timezone checks and actual dashboard HTTP200 passed. Live dashboard health/status HTTP200; real Forgejo GET /user authenticated as homelab-renovate. Existing approval boundary and secrets preserved.)
- [x] Observe an existing reviewer execution on the replacement runtime (Existing scheduled job renovate-major-reviewer-29865257 succeeded on retained Renovate97006d85. No manual bot run or approvals triggered.)

## Owned source

- `gitops/automation/renovate-agent.yaml`
- `gitops/automation/renovate-dashboard.yaml`
- `docs/runbooks/forgejo-automation-runtime.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `5a30461386c04e6195da04a3c911e2ff90633d1f72a59489707c99a3fefefbad`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- None recorded

## Next action

None, automation runtime replacement acceptance complete

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
