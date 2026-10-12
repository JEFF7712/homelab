# Agent Task: sovereign-lifecycle-rfc

Status: `complete`

Base commit: `840636cd9fe92da13ad25bc4108fa78adbdd11ae`

Checkpoint HEAD: `840636cd9fe92da13ad25bc4108fa78adbdd11ae`

Owner: `codex`

Session: `sovereign-lifecycle-rfc-20261008`

Exported at: `2026-10-08T06:37:36.247971+00:00`

Current HEAD at export: `840636cd9fe92da13ad25bc4108fa78adbdd11ae`

## Objective

Define sovereign lifecycle contracts, audit CI enrollment, and select the first stateless migration pilot

## Acceptance criteria

- [x] Document current Woodpecker enrollment restrictions and verify rejection behavior (docs/research/2026-10-08-sovereign-application-lifecycle-rfc.md and .agent-state/evidence/sovereign-lifecycle-rfc/)
- [x] Select a stateless pilot and document five lifecycle contracts and independent acceptance tests (docs/research/2026-10-08-sovereign-application-lifecycle-rfc.md and .agent-state/evidence/sovereign-lifecycle-rfc/)

## Owned source

- `docs/research/2026-10-08-sovereign-application-lifecycle-rfc.md`
- `docs/agent-tasks/sovereign-lifecycle-rfc.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `065be36d36e9aa992f53e817ea46bf289b61b33a28088499701f4b3277e4ea60`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `just check-changed`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-08T06:37:06.260379+00:00`, source fingerprint `4c20df08611542d0014857d33c6855d8e5a5425e80afd48bec974eeebafe3075`, evidence `/home/rupan/homelab/.agent-state/evidence/checks/verify-qu46ghzp.log`

## Next action

Implement the application catalog and policy branch against the reviewed RFC; shared enrollment and activation remain separate

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
