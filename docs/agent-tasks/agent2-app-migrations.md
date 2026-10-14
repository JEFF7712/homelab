# Agent Task: agent2-app-migrations

Status: `active`

Base commit: `ab4fb45a7f8ef97c7c79478bf6f2717ab390e9f6`

Checkpoint HEAD: `ab4fb45a7f8ef97c7c79478bf6f2717ab390e9f6`

Owner: `agent2`

Session: `agent2-app-migrations-20261009`

Exported at: `2026-10-09T19:35:18.736908+00:00`

Current HEAD at export: `ab4fb45a7f8ef97c7c79478bf6f2717ab390e9f6`

## Objective

Migrate assigned apps (soluble, pod-agent, bus-display backend+firmware, bookshelf, nixos CI, voice/automation producers) to Forgejo/Woodpecker with local artifacts

## Acceptance criteria

- [ ] Forgejo authoritative repos with history preserved

## Owned source

- `config/ci/applications.json`

## Remaining work

- discovery
- migration

## Verification

Current source fingerprint at export: `1abe1da37f5442f3f086b5af5974aa2fdb0b0d1a43b9782311a251269088a115`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- None recorded

## Next action

discover sources and create Forgejo repos

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
