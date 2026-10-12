# Agent Task: agent2-app-migrations-r2

Status: `active`

Base commit: `ab4fb45a7f8ef97c7c79478bf6f2717ab390e9f6`

Checkpoint HEAD: `ab4fb45a7f8ef97c7c79478bf6f2717ab390e9f6`

Owner: `agent2`

Session: `agent2-app-migrations-r2-20261009`

Exported at: `2026-10-10T08:01:52.094115+00:00`

Current HEAD at export: `691ce5d67378ec85a29227d0a9899c6f1dab72ae`

## Objective

Finish app migrations: Woodpecker enrollment, publishable contracts, strict supply validators, offline builds, firmware recovery, stateful restores, token revocation

## Acceptance criteria

- [ ] All 5 repos enrolled in Woodpecker with branch protection; no null IDs (or documented exception)

## Owned source

- `config/ci/applications.json`
- `scripts/ci/applications.py`

## Remaining work

- enrollment
- release lanes
- validators
- offline builds
- firmware
- recovery
- revocation

## Verification

Current source fingerprint at export: `f798ebf4059efe015e852fc4e504b476566651c351dcd399d1ba0e761196ca6f`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- None recorded

## Next action

Woodpecker enrollment under flock

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
