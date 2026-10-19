# Agent Task: projectctl-core

Status: `complete`

Base commit: `096a4e6a6a0417c1ff877ab3b652a0992f847c70`

Checkpoint HEAD: `096a4e6a6a0417c1ff877ab3b652a0992f847c70`

Owner: `codex`

Session: `projectctl-core-2026-10-15`

Exported at: `2026-10-16T00:16:40.643408+00:00`

Current HEAD at export: `096a4e6a6a0417c1ff877ab3b652a0992f847c70`

## Objective

Implement the offline projectctl protocol core: strict schemas, authorization, planning, durable recovery, and fake-service acceptance tests.

## Acceptance criteria

- [x] Strict versioned documents, canonicalization vectors, plan validation, and signed request-scoped approvals reject malformed or unauthorized input. (/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-ffkwaset.log; /home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-6ejztkl9.log; /home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-gmo0_mnz.log)
- [x] Journal and offline worker survive disconnect/crash, coalesce retries, reject conflicts, and stop writes on revocation/cancellation using fake services. (/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-ffkwaset.log; /home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-6ejztkl9.log; /home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-gmo0_mnz.log)
- [x] Changed Python code passes formatter, Ruff, pyright, and meaningful offline tests; scoped repository validation results and limitations are recorded. (/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-ffkwaset.log; /home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-6ejztkl9.log; /home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-gmo0_mnz.log)

## Owned source

- `scripts/projectctl/`
- `tests/test_projectctl.py`
- `tests/fixtures/projectctl/`
- `scripts/checks/python.sh`
- `docs/runbooks/projectctl.md`
- `AGENT_MAP.md`
- `docs/agent-tasks/projectctl-core.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `44bc7e44603a4944370ceb9a485f676efafd16a0d6a328a7f3a56e15c0a6f228`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `python -m unittest tests.test_projectctl -v`: exit 0, freshness `current`, superseded `false`, verified at `2026-10-16T00:12:28.042477+00:00`, source fingerprint `44bc7e44603a4944370ceb9a485f676efafd16a0d6a328a7f3a56e15c0a6f228`, evidence `/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-ffkwaset.log`
- `just check-changed`: exit 0, freshness `current`, superseded `false`, verified at `2026-10-16T00:15:58.986193+00:00`, source fingerprint `44bc7e44603a4944370ceb9a485f676efafd16a0d6a328a7f3a56e15c0a6f228`, evidence `/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-6ejztkl9.log`
- `pyright scripts/projectctl tests/test_projectctl.py`: exit 0, freshness `current`, superseded `false`, verified at `2026-10-16T00:16:13.454678+00:00`, source fingerprint `44bc7e44603a4944370ceb9a485f676efafd16a0d6a328a7f3a56e15c0a6f228`, evidence `/home/rupan/homelab/.worktrees/projectctl-protocol/.agent-state/evidence/checks/verify-gmo0_mnz.log`

## Next action

Next milestone: implement protected authority retrieval, registry transition verification, and read-only service adapters before enabling any live mutations.

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
