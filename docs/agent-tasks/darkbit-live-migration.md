# Agent Task: darkbit-live-migration

Status: `complete`

Base commit: `840636cd9fe92da13ad25bc4108fa78adbdd11ae`

Checkpoint HEAD: `ab4fb45a7f8ef97c7c79478bf6f2717ab390e9f6`

Owner: `codex`

Session: `darkbit-live-20261008`

Exported at: `2026-10-08T23:50:47.708415+00:00`

Current HEAD at export: `ab4fb45a7f8ef97c7c79478bf6f2717ab390e9f6`

## Objective

Activate application validation policy and verify Darkbit live Forgejo/Woodpecker migration

## Acceptance criteria

- [x] Policy PR merged after required green CI (Homelab PR103 merged a9d4566 after pipelines592/593; corrective PR104 merged 8e15e07 after600/601; agent resilience PR105 merged9068760 after604/605.)
- [x] Reviewed policy active on homelab-04 (Initial generation40 activation receipt .agent-state/evidence/darkbit-live-migration/initial-activation.json; visibility correction closure7kc2y6j0cmmqvvb4fbq53425knqrmgvf activated. Agent resilience closure nlnm93gwm3hf9mfmcf7sz3safxr4qdhp copied but not activated.)
- [x] Darkbit live success and failure validation plus protected branches verified (Darkbit PR1 merged6a6890666b119ec1b77c5cb1fd5b7e3b10946f55 after pipeline8 success. Test PR2: hostile repository YAML ignored, pipeline12 success; missing asset failed pipeline13, merge rejected405; restored asset pipeline16 success; PR2 closed. Rejection receipt .agent-state/evidence/darkbit-live-migration/red-merge-rejection.json.)
- [x] Agent restart resilience activated and recovery documentation published with temporary administration authority revoked (.agent-state/evidence/darkbit-live-migration/agent-restart-acceptance.json; main-validation.json (pipeline19 success); token-revocation.json; documentation-merge.json (PR109 merged f20d993 after required greenCI).)

## Owned source

- `config/ci/applications.json`
- `docs/runbooks/application-ci.md`
- `docs/agent-tasks/darkbit-live-migration.md`
- `.agent-state/worktrees/application-ci-live/docs/runbooks/application-ci.md`
- `.agent-state/worktrees/application-ci-live/.sops.yaml`
- `.agent-state/worktrees/application-ci-live/secrets/woodpecker-clone.sops.env`

## Remaining work

- None

## Verification

Current source fingerprint at export: `8a4a68e1deec3073e0f192f7c5c547cf979ff4c23ca395f4aecb964462deb475`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- None recorded

## Next action

Start the Darkbit local build and publication phase defined in docs/research/2026-10-08-sovereign-application-lifecycle-rfc.md; keep the existing GitHub publisher until the local release path is proven.

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
